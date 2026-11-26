"""
Role-based authentication for Momira Travel Platform.

Two roles, determined automatically at login:

  Admin   — logs in with the shared APP_PASSWORD.
            Sees everything: all suppliers, bulk tools, rollover, price refresh, etc.

  Supplier — logs in with their TC Supplier ID + email address.
             Validated against the live TC API (GET /suppliers/{id}).
             Must exist, email must match, and active must be True.
             Sees only their own products; can create new tickets/closed tours.

The login form presents two tabs: "Admin" and "Supplier".
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


def _issue_auth_cookie(role: str, supplier_id: int | None = None):
    token = _secrets_mod.token_urlsafe(32)
    expires_at = time.time() + _AUTH_SESSION_SECONDS
    record = {"expires_at": expires_at, "role": role}
    if supplier_id is not None:
        record["supplier_id"] = supplier_id
    supplier_email = st.session_state.get("_supplier_email")
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


def _validate_supplier(supplier_id: int, email: str) -> dict | None:
    """Validate supplier credentials against TC API.
    Returns the supplier record on success, None on failure."""
    from api_client import TravelCompositorAPI
    try:
        api = TravelCompositorAPI()
        url = f"{api.api_base_url}/suppliers/{supplier_id}"
        res = api._request("GET", url)
        if res.status_code == 404:
            return None
        if res.status_code != 200:
            return None
        supplier = res.json()
        if not isinstance(supplier, dict):
            return None
        if not supplier.get("active", False):
            return None
        tc_email = (supplier.get("email") or "").strip().lower()
        entered_email = email.strip().lower()
        if not tc_email or tc_email != entered_email:
            return None
        return supplier
    except Exception:
        return None


def _set_supplier_session(supplier: dict):
    st.session_state["_authenticated"] = True
    st.session_state["_auth_role"] = "supplier"
    st.session_state["_supplier_id"] = supplier.get("id")
    st.session_state["_supplier_name"] = (
        supplier.get("commercialName") or supplier.get("legalName") or f"Supplier {supplier.get('id')}"
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
    """Show the login form and block the app until authenticated.
    Call this at the top of app.py instead of the old password gate."""
    if st.session_state.get("_authenticated"):
        return

    cookie_record = _check_auth_cookie()
    if cookie_record:
        role = cookie_record.get("role", "admin")
        if role == "supplier":
            supplier_id = cookie_record.get("supplier_id")
            supplier_email = cookie_record.get("supplier_email", "")
            if supplier_id and supplier_email:
                supplier = _validate_supplier(supplier_id, supplier_email)
                if supplier:
                    _set_supplier_session(supplier)
                    return
                # Supplier no longer active/valid — fall through to login
            # Cookie had supplier role but no ID or validation failed
        else:
            _set_admin_session()
            return

    st.title("Momira Travel Platform")

    admin_tab, supplier_tab = st.tabs(["Admin", "Supplier"])

    with admin_tab:
        app_password = os.environ.get("APP_PASSWORD", "").strip()
        if not app_password:
            st.info("Admin login is not configured (APP_PASSWORD not set).")
        else:
            entered = st.text_input("Password", type="password", key="_admin_password_input")
            if st.button("Log in as Admin", type="primary", key="_admin_login_btn"):
                if entered == app_password:
                    _set_admin_session()
                    _issue_auth_cookie("admin")
                else:
                    st.error("Incorrect password.")

    with supplier_tab:
        st.markdown("Log in with your **Supplier ID** and **email address** as registered in the system.")
        col1, col2 = st.columns(2)
        with col1:
            sid = st.text_input("Supplier ID", key="_supplier_id_input", placeholder="e.g. 48940")
        with col2:
            semail = st.text_input("Email", key="_supplier_email_input", placeholder="your@email.com")

        if st.button("Log in as Supplier", type="primary", key="_supplier_login_btn"):
            if not sid or not semail:
                st.error("Please enter both your Supplier ID and email address.")
            else:
                try:
                    sid_int = int(sid.strip())
                except ValueError:
                    st.error("Supplier ID must be a number.")
                    sid_int = None
                if sid_int is not None:
                    with st.spinner("Verifying your credentials..."):
                        supplier = _validate_supplier(sid_int, semail)
                    if supplier:
                        _set_supplier_session(supplier)
                        _issue_auth_cookie("supplier", supplier_id=sid_int)
                        st.success(f"Welcome, {st.session_state['_supplier_name']}!")
                    else:
                        st.error(
                            "Login failed. Please check that:\n"
                            "- Your Supplier ID is correct\n"
                            "- Your email matches the one registered in the system\n"
                            "- Your supplier account is active\n\n"
                            "If you need help, contact Momira Travel."
                        )

    if not st.session_state.get("_authenticated"):
        st.stop()
