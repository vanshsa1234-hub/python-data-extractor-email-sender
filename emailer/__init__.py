"""
emailer package
===============
Exports the most commonly used functions so callers can do:
    from emailer import send_email, bulk_send, render_email
"""

from emailer.sender import (
    send_email,
    bulk_send,
    render_email,
    send_via_mailgun,
    init_db,
    log_email,
    daily_send_count,
    DEFAULT_TEMPLATE,
)
from emailer.tracker import (
    add_tracking_pixel,
    build_unsubscribe_link,
    record_open,
    record_unsubscribe,
    is_unsubscribed,
    manual_unsubscribe,
    remove_unsubscribe,
    get_unsubscribes,
    delete_sent_record,
    clear_all_sent_records,
    get_open_stats,
    run_tracking_server,
)
from emailer.ab_test import ABTest, run_ab_campaign
from emailer.followup import (
    enroll_leads,
    get_due_leads,
    run_drip_job,
    start_scheduler,
    get_sequence_stats,
    pause_lead,
    reactivate_lead,
    delete_lead_from_sequence,
    set_lead_step,
    get_all_sequence_leads,
    DRIP_SEQUENCE,
)

__all__ = [
    # sender
    "send_email", "bulk_send", "render_email", "send_via_mailgun",
    "init_db", "log_email", "daily_send_count", "DEFAULT_TEMPLATE",
    # tracker
    "add_tracking_pixel", "build_unsubscribe_link", "record_open",
    "record_unsubscribe", "is_unsubscribed", "manual_unsubscribe",
    "remove_unsubscribe", "get_unsubscribes", "delete_sent_record",
    "clear_all_sent_records", "get_open_stats", "run_tracking_server",
    # ab_test
    "ABTest", "run_ab_campaign",
    # followup
    "enroll_leads", "get_due_leads", "run_drip_job", "start_scheduler",
    "get_sequence_stats", "pause_lead", "reactivate_lead",
    "delete_lead_from_sequence", "set_lead_step", "get_all_sequence_leads",
    "DRIP_SEQUENCE",
]
