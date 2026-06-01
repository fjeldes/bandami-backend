"""
Script para crear o promocionar un usuario a admin.
Uso: python3 scripts/promote_admin.py email@example.com
"""

import sys
from app.db.supabase import get_supabase


def promote_to_admin(email: str):
    supabase = get_supabase()

    users = supabase.auth.admin.list_users()
    target = None
    for u in users:
        if u.email == email:
            target = u
            break

    if not target:
        print(f"User {email} not found.")
        return

    supabase.table("user_profiles").update({"role": "admin"}).eq("id", target.id).execute()
    print(f"User {email} ({target.id}) promoted to admin.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/promote_admin.py email@example.com")
        sys.exit(1)
    promote_to_admin(sys.argv[1])
