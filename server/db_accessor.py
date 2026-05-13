"""DB accessor — avoids circular imports between routes and main."""

_db_instance = None


def set_db(db):
    global _db_instance
    _db_instance = db


def get_db():
    return _db_instance
