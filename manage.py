"""Trusted operator command: python manage.py promote-admin existing@email.com."""
import argparse

from sqlalchemy import select

from db import SessionLocal
from models import Customer


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage account roles locally")
    parser.add_argument("command", choices=["promote-admin"])
    parser.add_argument("email", help="Exact email of an already registered account")
    args = parser.parse_args()
    with SessionLocal() as session:
        customer = session.scalar(select(Customer).where(Customer.email == args.email))
        if customer is None or customer.password_hash is None:
            parser.error("Register the account with a password first")
        customer.role = "admin"
        session.commit()
    print("Account promoted to admin")


if __name__ == "__main__":
    main()
