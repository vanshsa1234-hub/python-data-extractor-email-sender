"""
app.py
======
Phase 4 — Streamlit Dashboard (GUI)

A full point-and-click interface for the entire tool.

Tabs:
  1. 📥 Scraper     — paste URLs, scrape, preview results
  2. 🔗 LinkedIn    — search LinkedIn leads
  3. 📋 Leads       — view, filter, and manage your lead CSV
  4. 📧 Email       — compose & send bulk emails
  5. 💧 Drip        — manage follow-up sequences
  6. 📊 Analytics   — open rates, send stats, unsubscribes

Run:
    streamlit run app.py
"""

import streamlit as st
import pandas as pd
import sqlite3
import sys
import logging
from pathlib import Path
from datetime import datetime

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Scraper + Email Tool",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)
# ── Sidebar ───────────────────────────────────────────────────────────────────
# ── Multi-user session state ──────────────────────────────────────────────────
from admin_auth import (
    init_db, login_user, register_user,
    save_user_leads, get_user_leads, delete_user_lead, clear_user_leads,
    replace_user_leads, delete_emails_for_user,
    list_users, delete_user, change_password,
    get_user_lead_count, is_admin_user, verify_credentials
)

init_db()

if "user_id"       not in st.session_state: st.session_state.user_id       = None
if "username"      not in st.session_state: st.session_state.username      = ""
if "user_role"     not in st.session_state: st.session_state.user_role     = ""
if "logged_in"     not in st.session_state: st.session_state.logged_in     = False
if "auth_page"     not in st.session_state: st.session_state.auth_page     = "login"


def is_logged_in() -> bool:
    return st.session_state.get("logged_in", False)

def is_admin() -> bool:
    return st.session_state.get("user_role", "") == "admin"

def current_user_id() -> int:
    return st.session_state.get("user_id", -1)


# ── Gate: show login/register if not logged in ────────────────────────────────
if not is_logged_in():
    st.title("🚀 Python Data Extractor & Email Sender")
    st.markdown("---")

    # ── Normal login/register ──────────────────────────────────────────────────
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("🔑 Login")
        with st.form("login_form"):
            login_user_input = st.text_input("Username")
            login_pass_input = st.text_input("Password", type="password")
            login_btn        = st.form_submit_button("Login", type="primary")
        if login_btn:
            success, user = login_user(login_user_input, login_pass_input)
            if success:
                st.session_state.logged_in  = True
                st.session_state.user_id    = user["id"]
                st.session_state.username   = user["username"]
                st.session_state.user_role  = user["role"]
                st.success(f"✅ Welcome back, {user['username']}!")
                st.rerun()
            else:
                st.error("❌ Incorrect username or password.")

    with col_r:
        st.subheader("📝 Create Account")
        st.info("New here? Create a free account to start scraping and sending emails.")
        with st.form("register_form"):
            reg_username = st.text_input("Choose a username")
            reg_password = st.text_input("Choose a password", type="password")
            reg_confirm  = st.text_input("Confirm password", type="password")
            reg_btn      = st.form_submit_button("Create Account", type="primary")
        if reg_btn:
            if reg_password != reg_confirm:
                st.error("❌ Passwords don't match.")
            else:
                success, msg = register_user(reg_username, reg_password)
                if success:
                    st.success(f"✅ {msg} Please log in.")
                else:
                    st.error(f"❌ {msg}")

    st.markdown("---")
    st.caption("Your data is private — only you can see your scraped leads.")
    st.stop()


# ── Sidebar (only shown when logged in) ───────────────────────────────────────
with st.sidebar:
    st.title("🚀 Scraper & Emailer")
    st.markdown("---")

    # User info
    lead_count = get_user_lead_count(current_user_id())
    st.success(f"👤 {st.session_state.username}")
    st.caption(f"Role: {st.session_state.user_role} · Leads: {lead_count}")

    if st.button("🚪 Logout", key="sidebar_logout"):
        for key in ["logged_in","user_id","username","user_role"]:
            st.session_state[key] = None if key == "user_id" else ""
        st.session_state.logged_in = False
        st.rerun()

    st.markdown("---")
    st.caption("Navigation")
    # Admin tab only visible to admin users
    tabs = ["📥 Scraper", "🔗 LinkedIn", "📋 Leads", "📧 Email",
            "💧 Drip Sequences", "✅ Verify Emails", "📊 Analytics"]
    if is_admin():
        tabs.append("🔐 Admin")

    page = st.radio(
        "Go to",
        tabs,
        label_visibility="collapsed",
    )
    st.markdown("---")
    st.caption(f"📅 {datetime.now().strftime('%d %b %Y')}")
    st.markdown(
        """<div style="position:fixed;bottom:10px;left:20px;color:gray;font-size:14px;">
        Made by <b>Vansh Saini</b></div>""",
        unsafe_allow_html=True
    )


# ── Helper: load/save leads from USER'S DB partition ─────────────────────────
def load_leads_df(path: str = None) -> pd.DataFrame:
    """Load leads for the current logged-in user from the database."""
    EMPTY = pd.DataFrame(columns=["email", "phone", "website"])
    if not is_logged_in():
        return EMPTY
    rows = get_user_leads(current_user_id())
    if not rows:
        return EMPTY
    return pd.DataFrame(rows)


def save_leads_df(df: pd.DataFrame, path: str = None):
    """Save leads back to the user's DB partition."""
    if not is_logged_in() or df.empty:
        return
    leads = df.to_dict("records")
    save_user_leads(current_user_id(), leads)


# CSV file path still used for download buttons
LEADS_CSV_PATH = "data/leads.csv"


def get_db_conn(path: str = "data/sent_log.db") -> sqlite3.Connection:
    return sqlite3.connect(path)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════
if page == "📥 Scraper":
    st.title("📥 Web Scraper")
    st.markdown("Paste target URLs below — one per line. The scraper extracts emails, names, and phone numbers.")

    col1, col2 = st.columns([2, 1])
    with col1:
        raw_urls = st.text_area(
            "Target URLs (one per line)",
            placeholder="https://example.com/contact\nhttps://another.com/team",
            height=180,
        )
    with col2:
        st.markdown("**Options**")
        use_dynamic = st.checkbox("Use Playwright (JS sites)", value=False)
        if use_dynamic:
            st.warning(
                "⚠️ **Playwright mode**: Works only if `playwright install chromium` "
                "has been run in your terminal. If scraping returns 0 results, "
                "uncheck this — most sites work fine with static scraping."
            )
        max_retries = st.slider("Max retries per URL", 1, 5, 2)
        append_mode = st.checkbox("Append to existing leads.csv", value=True)

    if st.button("🚀 Start Scraping", type="primary"):
        urls = [u.strip() for u in raw_urls.splitlines() if u.strip()]
        if not urls:
            st.warning("Please enter at least one URL.")
        else:
            from scraper.web_scraper import bulk_scrape
            from scraper.cleaner import clean_leads

            with st.spinner(f"Scraping {len(urls)} URL(s)…"):
                raw = bulk_scrape(urls, dynamic=use_dynamic, max_retries=max_retries)

            flat = []
            for item in raw:
                names   = item.get("names", [])      # person names (e.g. "Ms. Deepti Vohra")
                phones  = item.get("phones", [])
                company = item.get("company", "")     # org/school name
                name    = names[0]  if names  else ""
                phone   = phones[0] if phones else ""
                for email in item.get("emails", []):
                    flat.append({"email": email, "name": name, "source": item["url"],
                                 "company": company, "phone": phone})

            cleaned = clean_leads(flat)

            if cleaned:
                new_df = pd.DataFrame(cleaned)
                # Save directly to this user's DB partition
                n_saved = save_user_leads(current_user_id(), cleaned)
                total   = get_user_lead_count(current_user_id())
                st.success(f"✅ Found {len(cleaned)} leads — {n_saved} new saved — {total} total in your account")
                st.dataframe(new_df, use_container_width=True)
            else:
                st.warning("⚠️ No valid email addresses found on those pages.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — LINKEDIN
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🔗 LinkedIn":
    st.title("🔗 LinkedIn Lead Scraper")
    st.warning("⚠️ Uses unofficial linkedin-api. Use a throwaway account. Risk of temporary ban.")

    col1, col2, col3 = st.columns(3)
    with col1:
        keyword  = st.text_input("Job Title / Keyword", placeholder="Marketing Manager")
        location = st.text_input("Location", placeholder="India")
    with col2:
        count      = st.number_input("Max Leads", min_value=5, max_value=100, value=20)
        li_email   = st.text_input("LinkedIn Email", type="default", placeholder="dummy@gmail.com")
    with col3:
        li_pass    = st.text_input("LinkedIn Password", type="password")
        hunter_key = st.text_input("Hunter.io API Key (optional)", type="password")

    if st.button("🔍 Search LinkedIn", type="primary"):
        if not keyword or not li_email or not li_pass:
            st.error("Fill in keyword, LinkedIn email and password.")
        else:
            from scraper.linkedin_scraper import search_people, enrich_leads_with_emails
            from scraper.cleaner import clean_leads

            with st.spinner("Searching LinkedIn…"):
                leads = search_people(keyword, location, count, li_email, li_pass)

            if leads:
                with st.spinner("Enriching emails via Hunter.io…"):
                    enriched = enrich_leads_with_emails(leads, api_key=hunter_key)
                cleaned = clean_leads(enriched)

                df = pd.DataFrame(cleaned)
                st.success(f"✅ Found {len(df)} valid leads")
                st.dataframe(df, use_container_width=True)

                if st.button("💾 Save to leads.csv"):
                    existing = load_leads_df()
                    combined = pd.concat([existing, df]).drop_duplicates(subset="email")
                    save_leads_df(combined)
                    st.success(f"Saved! Total leads: {len(combined)}")
            else:
                st.error("No leads found. Check credentials or keyword.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — LEADS MANAGER
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📋 Leads":
    st.title("📋 Lead Manager")

    df = load_leads_df()

    if df.empty:
        st.info("No leads yet. Use the Scraper or LinkedIn tab to collect leads.")
    else:
        # ── Filters (available to everyone) ───────────────────────────────────
        with st.expander("🔍 Filter leads"):
            search_email = st.text_input("Filter by email contains")
            search_phone = st.text_input("Filter by phone contains")

        filtered = df.copy()
        if search_email:
            filtered = filtered[filtered["email"].str.contains(search_email, case=False, na=False)]
        if search_phone and "phone" in filtered.columns:
            filtered = filtered[filtered["phone"].astype(str).str.contains(search_phone, case=False, na=False)]

        st.markdown(f"**{len(filtered)} leads** (of {len(df)} total)")
        st.dataframe(filtered, use_container_width=True, height=420)

        # ── Download — available to everyone ──────────────────────────────────
        csv_data = filtered.to_csv(index=False).encode()
        st.download_button("⬇️ Download filtered CSV", csv_data,
                           "leads_filtered.csv", "text/csv")

        # ── Each user manages their OWN leads ────────────────────────────────────
        st.markdown("---")
        st.markdown("### ⚙️ Manage Your Leads")

        col_del, col_clear = st.columns(2)
        with col_del:
            del_email = st.text_input("Delete a specific email")
            if st.button("🗑️ Delete this email", type="secondary"):
                if del_email.strip():
                    ok = delete_user_lead(current_user_id(), del_email.strip())
                    st.success(f"✅ Deleted {del_email}") if ok else st.warning(f"'{del_email}' not found")
                    st.rerun()
        with col_clear:
            confirm_clear = st.checkbox("I want to delete ALL my leads permanently")
            if st.button("☠️ Clear ALL my leads", type="primary", disabled=not confirm_clear):
                n = clear_user_leads(current_user_id())
                st.warning(f"Deleted {n} leads from your account.")
                st.rerun()

        st.markdown("**Import leads from CSV**")
        uploaded = st.file_uploader("Upload CSV (email, phone, website columns)", type=["csv"])
        if uploaded:
            new_df = pd.read_csv(uploaded)
            n = save_user_leads(current_user_id(), new_df.to_dict("records"))
            st.success(f"✅ Imported {n} new leads to your account")
            st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — EMAIL SENDER
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📧 Email":
    st.title("📧 Bulk Email Sender")

    df = load_leads_df()
    if df.empty:
        st.warning("No leads found. Add leads first via the Scraper or LinkedIn tab.")
        st.stop()

    st.markdown(f"**{len(df)} leads available**")

    # ── Sender credentials ────────────────────────────────────────────────────
    st.markdown("### 📬 Sender Credentials")
    st.info(
        "Gmail requires an **App Password**, not your regular password. "
        "Enable 2FA first → then go to "
        "[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) "
        "→ generate a 16-character App Password and paste it below."
    )
    cred1, cred2, cred3 = st.columns(3)
    with cred1:
        sender_email = st.text_input("Your Gmail address", placeholder="you@gmail.com",
                                      key="sender_email_input")
    with cred2:
        sender_app_password = st.text_input("Gmail App Password (16 chars)", type="password",
                                             placeholder="abcd efgh ijkl mnop",
                                             key="sender_pass_input")
    with cred3:
        sender_name = st.text_input("Sender display name", placeholder="Your Name",
                                     key="sender_name_input")

    st.markdown("---")

    # ── Email content & settings ──────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        subject_tmpl = st.text_input(
            "Subject line",
            value="Quick question about {{ company if company else 'your work' }}",
            help="Supports Jinja2 variables: {{ first_name }}, {{ company }}, {{ title }}"
        )
        custom_msg = st.text_area(
            "Email body message",
            value="I came across your profile and thought there might be a great opportunity for us to connect.",
            height=140,
        )
    with col2:
        st.markdown("**Send settings**")
        dry_run = st.checkbox("🧪 Dry run (don't actually send)", value=True)
        limit   = st.number_input("Max emails to send this run", 1, 200, 50)
        st.markdown("---")
        st.markdown("**Preview** (first lead)")
        if not df.empty:
            sample = df.iloc[0].to_dict()
            from jinja2 import Template
            try:
                preview_subj = Template(subject_tmpl).render(**sample,
                    first_name=(str(sample.get("name","")).split() or ["there"])[0])
                st.caption(f"Subject: **{preview_subj}**")
            except Exception:
                st.caption("Subject preview error — check Jinja2 syntax")

    if st.button("📤 Send Emails", type="primary"):
        if not dry_run and (not sender_email.strip() or not sender_app_password.strip()):
            st.error("❌ Enter your Gmail address and App Password above before sending.")
            st.stop()

        from emailer.sender import bulk_send
        from emailer.tracker import is_unsubscribed
        from scraper.cleaner import is_valid_email

        leads = df.to_dict("records")
        sendable = [
            l for l in leads[:limit]
            if l.get("email") and is_valid_email(str(l["email"]))
               and not is_unsubscribed(str(l["email"]))
        ]

        if not sendable:
            st.error("No valid sendable leads after filtering.")
        else:
            progress = st.progress(0, text="Sending…")
            with st.spinner(f"{'Dry-running' if dry_run else 'Sending'} {len(sendable)} emails…"):
                summary = bulk_send(
                    leads=sendable,
                    subject_template=subject_tmpl,
                    custom_message=custom_msg,
                    dry_run=dry_run,
                    override_email=sender_email.strip() or None,
                    override_password=sender_app_password.strip() or None,
                    override_sender_name=sender_name.strip() or None,
                )
            progress.progress(1.0, text="Done!")
            st.success(f"✅ {'Dry run' if dry_run else 'Send'} complete!")
            col_s, col_f, col_sk = st.columns(3)
            col_s.metric("Sent",    summary["sent"])
            col_f.metric("Failed",  summary["failed"])
            col_sk.metric("Skipped", summary["skipped"])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — DRIP SEQUENCES
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "💧 Drip Sequences":
    st.title("💧 Drip Sequence Manager")

    from emailer.followup import (
        enroll_leads, get_sequence_stats, get_due_leads,
        run_drip_job, pause_lead, reactivate_lead,
        delete_lead_from_sequence, set_lead_step,
        get_all_sequence_leads, DRIP_SEQUENCE, init_sequence_db,
    )

    # ── Stats ─────────────────────────────────────────────────────────────────
    stats = get_sequence_stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Enrolled", stats["total"])
    c2.metric("Active",         stats["active"])
    c3.metric("Completed",      stats["completed"])
    c4.metric("Paused",         stats["paused"])

    st.markdown("---")

    # ── Sequence overview ─────────────────────────────────────────────────────
    with st.expander("📋 View Drip Sequence Steps"):
        for i, (day, subj, body) in enumerate(DRIP_SEQUENCE, 1):
            st.markdown(f"**Step {i} — Day {day}**")
            st.markdown(f"- Subject: `{subj}`")
            st.markdown(f"- Message: {body[:100]}…")
            st.markdown("")

    # ── Sender credentials ────────────────────────────────────────────────────
    st.markdown("### 📬 Sender Credentials")
    st.info(
        "These credentials are used for every drip email sent from this tab — "
        "Day 0, Day 3, and Day 7. Gmail requires an **App Password** (not your "
        "regular password). Go to "
        "[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) "
        "to generate one."
    )
    dc1, dc2, dc3 = st.columns(3)
    with dc1:
        drip_email    = st.text_input("Your Gmail address", placeholder="you@gmail.com",
                                       key="drip_sender_email")
    with dc2:
        drip_password = st.text_input("Gmail App Password", type="password",
                                       placeholder="16-char app password",
                                       key="drip_sender_pass")
    with dc3:
        drip_name     = st.text_input("Sender display name", placeholder="Your Name",
                                       key="drip_sender_name")

    st.markdown("---")

    # ── Enroll + Run ──────────────────────────────────────────────────────────
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("➕ Enroll Leads")
        st.caption("Enroll your current leads into the sequence. Already-enrolled leads are skipped.")
        if st.button("➕ Enroll all my leads"):
            df_leads = load_leads_df()
            if df_leads.empty:
                st.warning("No leads found. Scrape some leads first.")
            else:
                n = enroll_leads(df_leads.to_dict("records"))
                st.success(f"✅ Enrolled {n} new leads into the drip sequence.")
                st.rerun()

    with col_b:
        st.subheader("▶️ Run Drip Job")
        st.caption("Sends the right step email to every lead that is due today.")
        dry = st.checkbox("🧪 Dry run (log only, don't actually send)", value=True, key="drip_dry")

        if st.button("▶️ Run drip job now", type="primary"):
            if not dry and (not drip_email.strip() or not drip_password.strip()):
                st.error("❌ Enter your Gmail address and App Password above before sending.")
            else:
                # Pass user leads for name/company personalisation
                user_leads_for_drip = load_leads_df().to_dict("records")
                with st.spinner("Running drip job…"):
                    summary = run_drip_job(
                        dry_run=dry,
                        sender_email=drip_email.strip() or None,
                        sender_password=drip_password.strip() or None,
                        sender_name=drip_name.strip() or None,
                        user_leads=user_leads_for_drip,
                    )

                col_s, col_sk, col_f = st.columns(3)
                col_s.metric("Sent",    summary["sent"])
                col_sk.metric("Skipped", summary["skipped"])
                col_f.metric("Failed",  summary["failed"])

                if summary["details"]:
                    st.dataframe(pd.DataFrame(summary["details"]), use_container_width=True)

                if summary["failed"] > 0:
                    st.error("Some emails failed — check your Gmail address and App Password above.")
                elif summary["sent"] == 0 and summary["skipped"] == 0:
                    st.info("No leads were due today.")
                else:
                    st.success(f"✅ Done! {'(Dry run — no real emails sent)' if dry else ''}")

    # ── Due today ─────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📅 Leads Due Today")
    due = get_due_leads()
    if due:
        due_df = pd.DataFrame(due)
        due_df["step_label"] = due_df["current_step"].apply(
            lambda s: f"Step {s+1} — Day {DRIP_SEQUENCE[s][0]}" if s < len(DRIP_SEQUENCE) else "Done"
        )
        st.dataframe(due_df, use_container_width=True)
    else:
        st.info("No leads are due today.")

    # ── Manage individual leads ───────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🛠️ Manage Leads in Sequence")

    all_seq_leads = get_all_sequence_leads()

    if all_seq_leads:
        seq_df = pd.DataFrame(all_seq_leads)
        st.dataframe(seq_df, use_container_width=True)

        st.markdown("**Pick a lead to manage:**")
        emails_in_seq  = [l["email"] for l in all_seq_leads]
        selected_email = st.selectbox("Lead email", emails_in_seq, key="drip_admin_select")

        current = next(l for l in all_seq_leads if l["email"] == selected_email)
        step_i  = current["current_step"]
        step_label = (
            f"Step {step_i+1} — Day {DRIP_SEQUENCE[step_i][0]}"
            if step_i < len(DRIP_SEQUENCE) else "Completed"
        )
        st.caption(
            f"Status: **{current['status']}** · "
            f"{step_label} · "
            f"Next send: **{current['next_send_at']}**"
        )

        a1, a2, a3, a4 = st.columns(4)

        with a1:
            if st.button("⏸️ Pause", key="drip_pause_btn"):
                pause_lead(selected_email)
                st.success(f"Paused {selected_email}")
                st.rerun()
        with a2:
            if st.button("▶️ Resume", key="drip_resume_btn"):
                ok = reactivate_lead(selected_email)
                st.success(f"Resumed {selected_email}" if ok else "Lead not found")
                st.rerun()
        with a3:
            new_step = st.selectbox(
                "Set step", list(range(len(DRIP_SEQUENCE))),
                format_func=lambda s: f"Step {s+1} — Day {DRIP_SEQUENCE[s][0]}",
                key="drip_step_select",
            )
            if st.button("✏️ Update Step", key="drip_update_step_btn"):
                set_lead_step(selected_email, new_step)
                st.success(f"{selected_email} → Step {new_step+1}, due today")
                st.rerun()
        with a4:
            if st.button("🗑️ Delete", key="drip_delete_btn", type="primary"):
                delete_lead_from_sequence(selected_email)
                st.success(f"Removed {selected_email} from sequence")
                st.rerun()
    else:
        st.info("No leads enrolled yet — click '➕ Enroll all my leads' above.")

    # ── Raw table ─────────────────────────────────────────────────────────────
    with st.expander("📂 View full sequence table (raw)"):
        conn = init_sequence_db()
        seq_df = pd.read_sql("SELECT * FROM drip_sequence ORDER BY next_send_at", conn)
        conn.close()
        if not seq_df.empty:
            st.dataframe(seq_df, use_container_width=True)
        else:
            st.info("No leads enrolled yet.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📊 Analytics":
    st.title("📊 Campaign Analytics")

    from emailer.tracker import get_open_stats

    stats = get_open_stats()

    # Top metrics
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Emails Sent",   stats["total_sent"])
    c2.metric("Total Opens",   stats["total_opens"])
    c3.metric("Unique Opens",  stats["unique_opens"])
    c4.metric("Open Rate",     f"{stats['open_rate_%']}%")
    c5.metric("Unsubscribes",  stats["unsubscribes"])

    st.markdown("---")

    db_path = "data/sent_log.db"
    if not Path(db_path).exists():
        st.info("No data yet — send some emails first.")
        st.stop()

    conn = get_db_conn(db_path)

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📬 Sends Over Time")
        try:
            df_sends = pd.read_sql(
                "SELECT sent_date, COUNT(*) as count FROM sent_emails "
                "WHERE status='sent' GROUP BY sent_date ORDER BY sent_date",
                conn,
            )
            if not df_sends.empty:
                st.bar_chart(df_sends.set_index("sent_date")["count"])
            else:
                st.info("No send data yet.")
        except Exception:
            st.info("No send data yet.")

    with col2:
        st.subheader("👁️ Opens Over Time")
        try:
            df_opens = pd.read_sql(
                "SELECT date(opened_at) as open_date, COUNT(*) as count "
                "FROM email_opens GROUP BY open_date ORDER BY open_date",
                conn,
            )
            if not df_opens.empty:
                st.bar_chart(df_opens.set_index("open_date")["count"])
            else:
                st.info("No open data yet.")
        except Exception:
            st.info("No open data yet.")

    # Status breakdown
    st.subheader("📋 Email Status Breakdown")
    try:
        df_status = pd.read_sql(
            "SELECT status, COUNT(*) as count FROM sent_emails GROUP BY status",
            conn,
        )
        if not df_status.empty:
            st.dataframe(df_status, use_container_width=True)
    except Exception:
        pass

    # Recent activity
    st.subheader("🕐 Recent Activity")
    try:
        df_recent = pd.read_sql(
            "SELECT email, subject, status, sent_at FROM sent_emails "
            "ORDER BY sent_at DESC LIMIT 20",
            conn,
        )
        if not df_recent.empty:
            st.dataframe(df_recent, use_container_width=True)
    except Exception:
        st.info("No activity yet.")

    # Unsubscribes
    st.subheader("🚫 Unsubscribes")
    try:
        df_unsub = pd.read_sql(
            "SELECT email, unsubbed_at FROM unsubscribes ORDER BY unsubbed_at DESC",
            conn,
        )
        if not df_unsub.empty:
            st.dataframe(df_unsub, use_container_width=True)
        else:
            st.success("No unsubscribes yet! 🎉")
    except Exception:
        pass

    conn.close()

    # ── Admin: manage unsubscribes & sent records ──────────────────────────
    st.markdown("---")
    st.subheader("🛠️ Manage Data")

    from emailer.tracker import manual_unsubscribe, remove_unsubscribe, delete_sent_record, clear_all_sent_records

    tab_unsub, tab_sent = st.tabs(["🚫 Unsubscribes", "📧 Sent Records"])

    with tab_unsub:
        ucol1, ucol2 = st.columns(2)
        with ucol1:
            st.markdown("**Add an unsubscribe manually**")
            new_unsub_email = st.text_input("Email to unsubscribe", key="manual_unsub_input")
            if st.button("🚫 Unsubscribe this email", key="manual_unsub_btn") and new_unsub_email:
                manual_unsubscribe(new_unsub_email)
                st.success(f"{new_unsub_email} marked as unsubscribed")
                st.rerun()

        with ucol2:
            st.markdown("**Remove an unsubscribe (re-permit sending)**")
            remove_unsub_email = st.text_input("Email to re-allow", key="remove_unsub_input")
            if st.button("✅ Remove unsubscribe", key="remove_unsub_btn") and remove_unsub_email:
                ok = remove_unsubscribe(remove_unsub_email)
                if ok:
                    st.success(f"{remove_unsub_email} can now receive emails again")
                else:
                    st.warning(f"{remove_unsub_email} was not found on the unsubscribe list")
                st.rerun()

    with tab_sent:
        st.markdown("**Delete a single sent-email record by ID**")
        st.caption("Find the ID in the 'Recent Activity' table above, or the raw table below.")

        conn2 = get_db_conn(db_path)
        try:
            df_all_sent = pd.read_sql(
                "SELECT id, email, subject, status, sent_at FROM sent_emails ORDER BY sent_at DESC",
                conn2,
            )
        except Exception:
            df_all_sent = pd.DataFrame()
        conn2.close()

        if not df_all_sent.empty:
            st.dataframe(df_all_sent, use_container_width=True)
            del_id = st.number_input("Record ID to delete", min_value=1, step=1, key="del_sent_id")
            if st.button("🗑️ Delete this record", key="del_sent_btn"):
                ok = delete_sent_record(int(del_id))
                st.success(f"Deleted record {del_id}" if ok else f"No record with id {del_id}")
                st.rerun()

            st.markdown("---")
            st.markdown("**⚠️ Danger zone**")
            confirm_clear = st.checkbox("I understand this deletes ALL sent-email history permanently", key="confirm_clear_sent")
            if st.button("🗑️ Clear ALL sent records", key="clear_all_sent_btn", type="primary", disabled=not confirm_clear):
                n = clear_all_sent_records()
                st.success(f"Deleted {n} sent-email records")
                st.rerun()
        else:
            st.info("No sent records yet.")

    if st.button("🔄 Refresh"):
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# VERIFY EMAILS TAB
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "✅ Verify Emails":
    st.title("✅ Email Verifier")
    st.markdown(
        "Verify emails are real before sending. "
        "Checks 3 layers: **Syntax → DNS/MX records → SMTP handshake** "
        "(no emails are sent during verification)."
    )

    # ── Session state keys for persisting verify results across reruns ──────────
    if "verify_csv_results" not in st.session_state:
        st.session_state.verify_csv_results = None
    if "verify_manual_results" not in st.session_state:
        st.session_state.verify_manual_results = None

    tab_csv, tab_manual = st.tabs(["📋 Verify from leads.csv", "✏️ Verify manually"])

    with tab_csv:
        df = load_leads_df()
        if df.empty:
            st.info("No leads found. Scrape some URLs first.")
        else:
            st.markdown(f"**{len(df)} leads in your account**")
            show_cols = [c for c in ["email", "phone", "website"] if c in df.columns]
            st.dataframe(df[show_cols].fillna(""), use_container_width=True, height=200)

            col1, col2, col3 = st.columns(3)
            with col1:
                smtp_check = st.checkbox("SMTP handshake check", value=True,
                    help="Most accurate but slower (~2-5s per email).")
            with col2:
                workers = st.slider("Parallel workers", 1, 10, 3)
            with col3:
                keep_risky = st.checkbox("Keep risky emails", value=True,
                    help="Risky = DNS passed but SMTP inconclusive.")

            if st.button("🔍 Verify All Emails", type="primary"):
                from scraper.email_verifier import verify_bulk
                emails_list = df["email"].dropna().tolist()
                progress_bar = st.progress(0, text="Verifying…")
                with st.spinner(f"Verifying {len(emails_list)} emails…"):
                    results = verify_bulk(emails_list, smtp_check=smtp_check,
                                          max_workers=workers, delay=0.3)
                progress_bar.progress(1.0, text="Done!")
                # ── Store in session state so the save button survives the next rerun ──
                st.session_state.verify_csv_results = results

            # ── Render results + save button from session state (persists across reruns) ──
            if st.session_state.verify_csv_results is not None:
                results = st.session_state.verify_csv_results

                from collections import Counter
                ver_df = pd.DataFrame(results)[["email", "status", "reason", "mx"]]
                st.dataframe(ver_df, use_container_width=True)

                counts = Counter(r["status"] for r in results)
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("✅ Valid",     counts.get("valid",     0))
                c2.metric("⚠️ Risky",     counts.get("risky",     0))
                c3.metric("🔄 Catch-all", counts.get("catch_all", 0))
                c4.metric("❌ Invalid",   counts.get("invalid",   0))
                c5.metric("⏱️ Timeout",   counts.get("timeout",   0))

                include     = ["valid", "catch_all"] + (["risky"] if keep_risky else [])
                valid_set   = {r["email"] for r in results if r["status"] in include}
                invalid_set = {r["email"] for r in results if r["status"] not in include}

                st.markdown("---")
                col_kept, col_removed = st.columns(2)
                col_kept.metric("✅ Emails kept", len(valid_set))
                col_removed.metric("❌ Will be removed", len(invalid_set))

                if invalid_set:
                    with st.expander(f"🗑️ {len(invalid_set)} emails that will be removed"):
                        st.write(sorted(invalid_set))

                if st.button("💾 Save — remove invalid emails from my leads", type="primary"):
                    removed = delete_emails_for_user(current_user_id(), list(invalid_set))
                    st.session_state.verify_csv_results = None   # clear after save
                    st.success(
                        f"✅ Done! Removed {removed} invalid email(s). "
                        f"{len(valid_set)} verified leads remain. Check the 📋 Leads tab."
                    )
                    st.rerun()

    with tab_manual:
        st.markdown("Enter emails one per line to verify:")
        raw_emails  = st.text_area("Emails", height=150,
                                    placeholder="principal@school.com\ninfo@college.edu")
        smtp_manual = st.checkbox("SMTP check", value=True, key="smtp_manual")

        if st.button("🔍 Verify", type="primary", key="verify_manual"):
            from scraper.email_verifier import verify_bulk
            emails_list = [e.strip() for e in raw_emails.splitlines() if e.strip()]
            if not emails_list:
                st.warning("Enter at least one email.")
            else:
                with st.spinner(f"Verifying {len(emails_list)} emails…"):
                    results = verify_bulk(emails_list, smtp_check=smtp_manual, max_workers=3)
                # ── Store in session state so the save button survives the next rerun ──
                st.session_state.verify_manual_results = results

        # ── Render results + save button from session state ──────────────────────
        if st.session_state.verify_manual_results is not None:
            results = st.session_state.verify_manual_results

            from collections import Counter
            ver_df = pd.DataFrame(results)[["email", "status", "reason", "mx"]]
            st.dataframe(ver_df, use_container_width=True)

            counts_m = Counter(r["status"] for r in results)
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            mc1.metric("✅ Valid",     counts_m.get("valid",     0))
            mc2.metric("⚠️ Risky",     counts_m.get("risky",     0))
            mc3.metric("🔄 Catch-all", counts_m.get("catch_all", 0))
            mc4.metric("❌ Invalid",   counts_m.get("invalid",   0))
            mc5.metric("⏱️ Timeout",   counts_m.get("timeout",   0))

            keep_risky_manual = st.checkbox(
                "Include risky emails when saving", value=True, key="keep_risky_manual"
            )
            include_m = ["valid", "catch_all"] + (["risky"] if keep_risky_manual else [])
            saveable  = [r for r in results if r["status"] in include_m]

            if saveable:
                st.markdown(f"**{len(saveable)} email(s)** ready to save.")
                if st.button("💾 Save verified emails to my leads", type="primary", key="save_manual_verified"):
                    new_leads = [{"email": r["email"], "phone": "", "website": ""} for r in saveable]
                    inserted  = save_user_leads(current_user_id(), new_leads)
                    already   = len(saveable) - inserted
                    st.session_state.verify_manual_results = None   # clear after save
                    msg = f"✅ Added {inserted} new verified email(s) to your leads."
                    if already:
                        msg += f" ({already} already existed and were skipped.)"
                    msg += " Check the 📋 Leads tab."
                    st.success(msg)
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN TAB
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🔐 Admin":
    st.title("🔐 Admin Panel")

    if not is_admin():
        st.error("🔒 Access denied.")
        st.stop()

    st.success(f"✅ Admin: **{st.session_state.username}**")
    st.markdown("---")

    tab_overview, tab_user_data, tab_manage, tab_settings = st.tabs([
        "📊 Overview", "👁️ User Data", "👥 Manage Users", "⚙️ Settings"
    ])

    # ── Overview ──────────────────────────────────────────────────────────────
    with tab_overview:
        st.subheader("📊 Platform Overview")
        users = list_users()
        total_users = len(users)
        total_leads = sum(get_user_lead_count(u["id"]) for u in users)

        # Sent email stats across all users
        import sqlite3 as _sql
        import config as _cfg
        try:
            _conn2 = _sql.connect("data/admin.db")
            total_sent = _conn2.execute(
                "SELECT COUNT(*) FROM user_sent_emails WHERE status='sent'"
            ).fetchone()[0]
            _conn2.close()
        except Exception:
            total_sent = 0

        c1, c2, c3 = st.columns(3)
        c1.metric("👥 Total Users",  total_users)
        c2.metric("📋 Total Leads",  total_leads)
        c3.metric("📧 Emails Sent",  total_sent)

        st.markdown("---")
        st.subheader("Users at a glance")
        if users:
            rows = []
            for u in users:
                import sqlite3 as _sql2
                try:
                    _c = _sql2.connect("data/admin.db")
                    sent = _c.execute(
                        "SELECT COUNT(*) FROM user_sent_emails WHERE user_id=? AND status='sent'",
                        (u["id"],)
                    ).fetchone()[0]
                    drip = _c.execute(
                        "SELECT COUNT(*) FROM drip_sequence WHERE email IN "
                        "(SELECT email FROM user_leads WHERE user_id=?) AND status='active'",
                        (u["id"],)
                    ).fetchone()[0]
                    _c.close()
                except Exception:
                    sent = drip = 0
                rows.append({
                    "username":    u["username"],
                    "role":        u["role"],
                    "leads":       get_user_lead_count(u["id"]),
                    "emails_sent": sent,
                    "drip_active": drip,
                    "joined":      u["created_at"],
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # ── View any user's data ──────────────────────────────────────────────────
    with tab_user_data:
        st.subheader("👁️ View Any User's Data")
        users = list_users()
        user_map = {u["username"]: u["id"] for u in users}

        selected_user = st.selectbox("Select a user", list(user_map.keys()),
                                      key="admin_view_user")
        selected_uid  = user_map[selected_user]

        view_tab1, view_tab2, view_tab3 = st.tabs(["📋 Leads", "📧 Emails Sent", "💧 Drip Status"])

        with view_tab1:
            user_leads = get_user_leads(selected_uid)
            if user_leads:
                leads_df = pd.DataFrame(user_leads)
                st.markdown(f"**{len(leads_df)} leads for @{selected_user}**")
                st.dataframe(leads_df, use_container_width=True)
                csv_dl = leads_df.to_csv(index=False).encode()
                st.download_button(f"⬇️ Download {selected_user}'s leads",
                                   csv_dl, f"{selected_user}_leads.csv", "text/csv")
            else:
                st.info(f"@{selected_user} has no leads yet.")

        with view_tab2:
            try:
                import sqlite3 as _sql3
                _c3 = _sql3.connect("data/admin.db")
                sent_rows = _c3.execute(
                    "SELECT email, subject, status, sent_at FROM user_sent_emails "
                    "WHERE user_id=? ORDER BY sent_at DESC LIMIT 50",
                    (selected_uid,)
                ).fetchall()
                _c3.close()
                if sent_rows:
                    sent_df = pd.DataFrame(sent_rows,
                                           columns=["email","subject","status","sent_at"])
                    st.markdown(f"**{len(sent_df)} recent sends for @{selected_user}**")
                    st.dataframe(sent_df, use_container_width=True)
                else:
                    st.info(f"@{selected_user} hasn't sent any emails yet.")
            except Exception as e:
                st.info(f"No email log found: {e}")

        with view_tab3:
            try:
                import sqlite3 as _sql4
                _c4 = _sql4.connect(_cfg.SENT_LOG_DB)
                user_emails = [l["email"] for l in get_user_leads(selected_uid)]
                if user_emails:
                    placeholders = ",".join("?" * len(user_emails))
                    drip_rows = _c4.execute(
                        f"SELECT email, current_step, status, next_send_at FROM drip_sequence "
                        f"WHERE email IN ({placeholders})",
                        user_emails
                    ).fetchall()
                    _c4.close()
                    if drip_rows:
                        drip_df = pd.DataFrame(drip_rows,
                                               columns=["email","step","status","next_send"])
                        st.markdown(f"**Drip sequences for @{selected_user}**")
                        st.dataframe(drip_df, use_container_width=True)
                    else:
                        st.info(f"@{selected_user} has no active drip sequences.")
                else:
                    st.info(f"@{selected_user} has no leads to check drip for.")
            except Exception as e:
                st.info(f"No drip data found: {e}")

    # ── Manage Users ──────────────────────────────────────────────────────────
    with tab_manage:
        st.subheader("👥 Manage User Accounts")
        users = list_users()
        if users:
            for u in users:
                col_info, col_role, col_del = st.columns([3, 1, 1])
                col_info.markdown(
                    f"**{u['username']}** · `{u['role']}` · "
                    f"{get_user_lead_count(u['id'])} leads"
                )
                # Promote/demote role
                new_role = "user" if u["role"] == "admin" else "admin"
                if col_role.button(
                    f"→ {new_role}", key=f"role_{u['id']}",
                    disabled=(u["username"] == st.session_state.username)
                ):
                    from admin_auth import _conn as _auth_conn, init_db as _init
                    _init()
                    _ac = _auth_conn()
                    _ac.execute("UPDATE users SET role=? WHERE id=?", (new_role, u["id"]))
                    _ac.commit()
                    _ac.close()
                    st.success(f"{u['username']} is now {new_role}")
                    st.rerun()

                if col_del.button(
                    "🗑️", key=f"del_{u['id']}",
                    disabled=(u["username"] == st.session_state.username)
                ):
                    delete_user(u["id"])
                    st.success(f"Deleted {u['username']}")
                    st.rerun()

    # ── Settings ──────────────────────────────────────────────────────────────
    with tab_settings:
        st.subheader("⚙️ Change Admin Password")
        with st.form("admin_change_password"):
            old_pw  = st.text_input("Current password", type="password")
            new_pw  = st.text_input("New password",     type="password")
            new_pw2 = st.text_input("Confirm password", type="password")
            pw_btn  = st.form_submit_button("🔑 Change Password")
        if pw_btn:
            if new_pw != new_pw2:
                st.error("Passwords don't match.")
            elif len(new_pw) < 6:
                st.error("Minimum 6 characters.")
            elif change_password(st.session_state.username, old_pw, new_pw):
                st.success("✅ Password changed.")
            else:
                st.error("❌ Current password is incorrect.")

