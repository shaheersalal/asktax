from app.db.base import Base
from app.db.session import engine
import app.models.user
import app.models.document
import app.models.query


def init_db():
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    print("Creating tables...")
    init_db()
    print("Tables created successfully")