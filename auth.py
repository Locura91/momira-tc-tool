"""
Role-based authentication for Momira Travel Platform.

Unified login form: email + password. The system determines the role automatically:

  Admin    — email + APP_PASSWORD. Sees everything.
  Supplier — email + their TC Supplier ID (as "password").
             Validated against the live TC API. Must exist, email must match, active.

Session state keys set on success:
  _authenticated  = True
  _auth_role      = "admin" | "supplier"
  _supplier_id    = int (supplier only)
  _supplier_name  = str (supplier only, commercialName from TC)
  _supplier_email = str (supplier only)
"""

import os
import time
import secrets as _secrets_mod
import streamlit as st
import platform_store as _platform_store

_AUTH_COOKIE_NAME = "momira_auth_token"
_AUTH_SESSION_SECONDS = 24 * 60 * 60  # 24 hours


def _issue_auth_cookie(role: str, supplier_id: int | None = None,
                       supplier_email: str | None = None):
    token = _secrets_mod.token_urlsafe(32)
    expires_at = time.time() + _AUTH_SESSION_SECONDS
    record = {"expires_at": expires_at, "role": role}
    if supplier_id is not None:
        record["supplier_id"] = supplier_id
    if supplier_email:
        record["supplier_email"] = supplier_email
    _platform_store.set("auth_sessions", token, record)
    import streamlit.components.v1 as _components
    _components.html(
        f"""<script>
        document.cookie = "{_AUTH_COOKIE_NAME}={token}; max-age={_AUTH_SESSION_SECONDS}; path=/; SameSite=Lax";
        </script>""",
        height=0,
    )


def _check_auth_cookie() -> dict | None:
    try:
        token = st.context.cookies.get(_AUTH_COOKIE_NAME)
    except Exception:
        token = None
    if not token:
        return None
    record = _platform_store.get("auth_sessions", token)
    if not record or not isinstance(record, dict):
        return None
    if time.time() > record.get("expires_at", 0):
        _platform_store.delete("auth_sessions", token)
        return None
    return record


def _validate_supplier_by_id(supplier_id: int, email: str) -> dict | None:
    """Validate supplier by ID + email against TC API."""
    from api_client import TravelCompositorAPI
    try:
        api = TravelCompositorAPI()
        url = f"{api.api_base_url}/suppliers/{supplier_id}"
        res = api._request("GET", url)
        if res.status_code not in (200,):
            return None
        supplier = res.json()
        if not isinstance(supplier, dict):
            return None
        if not supplier.get("active", False):
            return None
        tc_email = (supplier.get("email") or "").strip().lower()
        if not tc_email or tc_email != email.strip().lower():
            return None
        return supplier
    except Exception:
        return None


def _find_supplier_by_email(email: str) -> dict | None:
    """Look up an active supplier by email from the full supplier list."""
    from api_client import TravelCompositorAPI
    try:
        api = TravelCompositorAPI()
        suppliers = api.get_all_suppliers()
        email_lower = email.strip().lower()
        for s in suppliers:
            tc_email = (s.get("email") or "").strip().lower()
            if tc_email == email_lower and s.get("active", False):
                return s
        return None
    except Exception:
        return None


def _set_supplier_session(supplier: dict):
    st.session_state["_authenticated"] = True
    st.session_state["_auth_role"] = "supplier"
    st.session_state["_supplier_id"] = supplier.get("id")
    st.session_state["_supplier_name"] = (
        supplier.get("commercialName") or supplier.get("legalName")
        or f"Supplier {supplier.get('id')}"
    )
    st.session_state["_supplier_email"] = (supplier.get("email") or "").strip().lower()


def _set_admin_session():
    st.session_state["_authenticated"] = True
    st.session_state["_auth_role"] = "admin"


def is_admin() -> bool:
    return st.session_state.get("_auth_role") == "admin"


def is_supplier() -> bool:
    return st.session_state.get("_auth_role") == "supplier"


def get_supplier_id() -> int | None:
    return st.session_state.get("_supplier_id")


def get_supplier_name() -> str | None:
    return st.session_state.get("_supplier_name")


def login_gate():
    """Unified login form: email + password. Role determined automatically.
    - If password matches APP_PASSWORD → admin
    - If password is a number and matches a supplier ID for that email → supplier
    """
    if st.session_state.get("_authenticated"):
        return

    cookie_record = _check_auth_cookie()
    if cookie_record:
        role = cookie_record.get("role", "admin")
        if role == "supplier":
            supplier_id = cookie_record.get("supplier_id")
            supplier_email = cookie_record.get("supplier_email", "")
            if supplier_id and supplier_email:
                supplier = _validate_supplier_by_id(supplier_id, supplier_email)
                if supplier:
                    _set_supplier_session(supplier)
                    return
        else:
            _set_admin_session()
            return

    import pathlib as _pathlib
    _logo_path = _pathlib.Path(__file__).parent / "static" / "momira-logo.png"
    if _logo_path.exists():
        st.image(str(_logo_path), width=250)
    st.title("Momira Travel Platform")
    st.write("")

    email = st.text_input("Email", key="_login_email", placeholder="your@email.com")
    password = st.text_input("Password", type="password", key="_login_password",
                             placeholder="Your password or Supplier ID")

    if st.button("Log in", type="primary", key="_login_btn"):
        if not email or not password:
            st.error("Please enter your email and password.")
        else:
            app_password = os.environ.get("APP_PASSWORD", "").strip()

            # Try admin login first: password matches APP_PASSWORD
            if app_password and password == app_password:
                _set_admin_session()
                _issue_auth_cookie("admin")
                return

            # Try supplier login: password is their Supplier ID (a number)
            try:
                sid_int = int(password.strip())
            except ValueError:
                sid_int = None

            if sid_int is not None:
                with st.spinner("Verifying your credentials..."):
                    supplier = _validate_supplier_by_id(sid_int, email)
                if supplier:
                    _set_supplier_session(supplier)
                    _issue_auth_cookie("supplier", supplier_id=sid_int,
                                       supplier_email=email.strip().lower())
                    st.success(f"Welcome, {st.session_state['_supplier_name']}!")
                    return
                else:
                    st.error(
                        "Login failed. Please check that your email and credentials "
                        "are correct and that your account is active.\n\n"
                        "If you need help, contact Momira Travel."
                    )
            else:
                st.error(
                    "Login failed. Please check that your email and credentials "
                    "are correct.\n\n"
                    "If you need help, contact Momira Travel."
                )

    if not st.session_state.get("_authenticated"):
        st.stop()
