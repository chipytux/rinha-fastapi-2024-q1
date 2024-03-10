import os
from datetime import datetime
from enum import Enum
from typing import Annotated
from typing import AsyncGenerator

from fastapi import Depends, HTTPException
from fastapi import FastAPI
from fastapi import status
from fastapi.responses import ORJSONResponse
from pydantic import (
    BaseModel,
    PositiveInt,
    Field,
    computed_field,
    ConfigDict,
    TypeAdapter,
)
from sqlalchemy import (
    String,
    Column,
    Integer,
    ForeignKey,
    func,
    DateTime,
    AsyncAdaptedQueuePool,
)
from sqlalchemy import text, insert, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import contains_eager
from sqlalchemy.orm import relationship

# APPLICATION
app = FastAPI(docs_url=None, redoc_url=None)


# DATABASE
engine: AsyncEngine = create_async_engine(
    os.environ.get("DATABASE_URL"),
    pool_size=int(os.environ.get("POOL_SIZE")),
    max_overflow=int(os.environ.get("MAX_OVERFLOW")),
    echo=False,
    echo_pool=False,
    poolclass=AsyncAdaptedQueuePool,
)

SESSION_MAKER = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SESSION_MAKER.begin() as session:
        yield session


# MODELS
Base = declarative_base()


class CustomerDB(Base):
    __tablename__ = "customer"

    id = Column(Integer, primary_key=True)
    limite = Column(Integer)
    saldo = Column(Integer)

    transactions = relationship(
        "TransactionDB", lazy="noload", uselist=True, back_populates="customers"
    )


class TransactionDB(Base):
    __tablename__ = "transaction"

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customer.id"))
    descricao = Column(String(10))
    realizada_em = Column(DateTime, nullable=False, default=func.now())
    tipo = Column(String(1))
    valor = Column(Integer)

    customers = relationship(
        CustomerDB, lazy="noload", uselist=False, back_populates="transactions"
    )


# Schemas


FromAttributes = ConfigDict(from_attributes=True)


class TransactionType(str, Enum):
    CREDIT = "c"
    DEBIT = "d"


class TransactionCreate(BaseModel):
    valor: PositiveInt
    tipo: TransactionType
    descricao: Annotated[str, Field(min_length=1, max_length=10)]

    @property
    def credit(self) -> int:
        return self.valor if self.tipo == TransactionType.CREDIT else -self.valor


class Statement(BaseModel, **FromAttributes):
    model_config = ConfigDict(populate_by_name=True)

    limite: int
    saldo: Annotated[int, Field(alias="total")]

    @computed_field
    @property
    def data_extrato(self) -> datetime:
        return datetime.now()


class Transaction(BaseModel, **FromAttributes):
    descricao: str
    realizada_em: datetime
    tipo: str
    valor: int


Transactions = TypeAdapter(list[Transaction])


class CustomerStatementResponse(BaseModel):
    saldo: Statement
    ultimas_transacoes: list[Transaction]

    @classmethod
    def from_customer_db(cls, customer: CustomerDB) -> "CustomerStatementResponse":
        return cls.model_construct(
            saldo=Statement.model_validate(customer),
            ultimas_transacoes=Transactions.validate_python(customer.transactions),
        )


# Routes - Onde a mágica acontece, leia se gambiarra
def check_customer_id(customer_id: int) -> None:
    if not (1 <= customer_id <= 5):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


@app.post("/clientes/{customer_id}/transacoes")
async def create_transaction(
    customer_id: int,
    transaction_create: TransactionCreate,
    session: AsyncSession = Depends(get_session),
) -> ORJSONResponse:
    check_customer_id(customer_id)

    result = await session.execute(
        text(f"SELECT limite, saldo FROM customer WHERE id = {customer_id} FOR UPDATE")
    )

    limite, saldo = result.one()

    if (
        transaction_create.tipo == TransactionType.DEBIT
        and transaction_create.valor > limite + saldo
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

    novo_saldo = saldo + transaction_create.credit

    await session.execute(
        text(f"UPDATE customer SET saldo = {novo_saldo} WHERE id = {customer_id}")
    )

    await session.commit()

    await session.execute(
        insert(TransactionDB).values(
            customer_id=customer_id, **transaction_create.model_dump()
        )
    )

    return ORJSONResponse(content={"limite": limite, "saldo": novo_saldo})


@app.get("/clientes/{customer_id}/extrato")
async def get_customer_statement(
    customer_id: int, session: AsyncSession = Depends(get_session)
) -> CustomerStatementResponse:
    check_customer_id(customer_id)

    query = (
        select(CustomerDB)
        .join(TransactionDB, isouter=True)
        .options(contains_eager(CustomerDB.transactions))
        .where(CustomerDB.id == customer_id)
        .order_by(TransactionDB.id.desc())
        .limit(10)
    )

    result = await session.execute(query)

    customer = result.unique().scalars().one()

    return CustomerStatementResponse.from_customer_db(customer)
