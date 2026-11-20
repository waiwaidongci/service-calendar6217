from sqlalchemy import Column,Integer,String,JSON,Float,UniqueConstraint
from src.config.database import Base
class SampleBatch(Base):
 __tablename__='sample_batches'
 id=Column(Integer,primary_key=True); run_id=Column(Integer); request_key=Column(String(80),unique=True); quotas=Column(JSON); selected=Column(JSON); overdue=Column(Integer); created=Column(Float); actor=Column(String(120))
class SampleOccupation(Base):
 __tablename__='sample_occupations'
 id=Column(Integer,primary_key=True); month=Column(String(120)); order_no=Column(String(120)); batch_id=Column(Integer)
 __table_args__=(UniqueConstraint('month','order_no',name='uq_sample_month_order'),)
