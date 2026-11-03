import os
os.environ.setdefault('DATABASE_URL','sqlite:///./test_papertrade.db')
from main import Base, SessionLocal, User, ph, engine
import pytest
@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        s.add(User(username='admin',password_hash=ph.hash('test-admin-password-123!')))
        s.commit()
    yield
