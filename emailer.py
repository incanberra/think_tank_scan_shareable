import os
import json
import smtplib
import ssl
from datetime import datetime, timezone
from email.utils import make_msgid
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import config
import scan_runtime
from state_storage import atomic_json_write, locked


def format_run_date_for_subject(run_date_str):
    try:
        return datetime.strptime(run_date_str, "%Y-%m-%d").strftime("%d %b %Y")
    except Exception:
        return run_date_str


def model_short_name(model_name):
    if not model_name:
        return "primary model"
    return model_name.split("/")[-1].replace("-", " ")


def build_email_subject(run_date_str, model_name=None, summary_counts=None, is_comparison=False):
    date_part = format_run_date_for_subject(run_date_str)
    if is_comparison:
        model_part = "model comparison"
    else:
        model_part = model_short_name(model_name)
    total_items = ""
    if summary_counts:
        total = sum(int(summary_counts.get(key, 0)) for key in ["reports", "podcasts", "events"])
        total_items = f" - {total} items"
    run = scan_runtime.current()
    if run and run.prior_delivered:
        return f"Think Tank Scan supplement - {date_part}{total_items.replace(" items", " additional items")} - {run.prior_delivered} previously delivered"
    return f"Think Tank Scan - {date_part} - {model_part}{total_items}"


def parse_recipients(value):
    if isinstance(value, (list, tuple, set)):
        raw_parts = value
    else:
        raw_parts = str(value or "").replace(";", ",").split(",")
    return [part.strip() for part in raw_parts if part and part.strip()]


DELIVERY_FILE = "email-delivery.json"
SMTP_TIMEOUT = 30


def queue_saved_email(directory, run_date, subject, html_path, pdf_path, model_reports):
    directory = Path(directory)
    path = directory / DELIVERY_FILE
    if path.exists():
        raise ValueError("An email delivery record already exists for this edition")
    recipients = list(dict.fromkeys(parse_recipients(config.SMTP_RECEIVER_EMAIL)))
    if not recipients or not config.SMTP_SENDER_EMAIL:
        raise ValueError("Configure an SMTP sender and recipients before sending an edition")
    atomic_json_write(path, {
        "run_date": run_date, "subject": subject, "message_id": make_msgid(),
        "sender": config.SMTP_SENDER_EMAIL, "recipients": dict.fromkeys(recipients, "pending"),
        "html": Path(html_path).name, "pdf": Path(pdf_path).name,
        "model_reports": model_reports, "attempts": [], "ledger_recorded": False,
    })


@locked
def deliver_saved_email(directory, retry_uncertain=False):
    """Retry recipients without an acceptance receipt using the original edition."""
    import seen_ledger

    directory = Path(directory).resolve()
    record_path = directory / DELIVERY_FILE
    record = json.loads(record_path.read_text(encoding="utf-8"))
    states = record["recipients"]
    send_to = [
        recipient for recipient, status in states.items()
        if status in {"pending", "refused"} or (retry_uncertain and status == "uncertain")
    ]
    if send_to:
        if record["sender"] != config.SMTP_SENDER_EMAIL or not config.SMTP_SENDER_PASSWORD:
            raise RuntimeError("The saved edition requires its original SMTP sender and a valid password")
        html_path = directory / record["html"]
        pdf_path = directory / record["pdf"]
        msg = MIMEMultipart("mixed")
        msg["From"] = record["sender"]
        msg["To"] = ", ".join(states)
        msg["Subject"] = record["subject"]
        msg["Message-ID"] = record["message_id"]
        msg.attach(MIMEText(html_path.read_text(encoding="utf-8"), "html", "utf-8"))
        pdf = MIMEBase("application", "pdf")
        pdf.set_payload(pdf_path.read_bytes())
        encoders.encode_base64(pdf)
        pdf.add_header("Content-Disposition", "attachment", filename=pdf_path.name)
        msg.attach(pdf)
        wire_message = msg.as_string()

        attempt = {"at": datetime.now(timezone.utc).isoformat(), "recipients": send_to, "status": "connecting"}
        record["attempts"].append(attempt)
        atomic_json_write(record_path, record)
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=SMTP_TIMEOUT) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(record["sender"], config.SMTP_SENDER_PASSWORD)
                # A disconnect during DATA leaves acceptance unknown. Persist
                # before handing the message to the server.
                attempt["status"] = "uncertain"
                states.update(dict.fromkeys(send_to, "uncertain"))
                atomic_json_write(record_path, record)
                try:
                    refusals = server.sendmail(record["sender"], send_to, wire_message)
                except smtplib.SMTPRecipientsRefused as exc:
                    refusals = exc.recipients
                except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
                    refusals = dict.fromkeys(send_to, (exc.smtp_code, b""))
                attempt["smtp_refusals"] = {address: detail[0] for address, detail in refusals.items()}
                for recipient in send_to:
                    states[recipient] = "refused" if recipient in refusals else "accepted"
                attempt["status"] = "recorded"
                atomic_json_write(record_path, record)
        except OSError as exc:
            if attempt["status"] == "connecting":
                attempt["status"] = "connection_failed"
            attempt["error_type"] = type(exc).__name__
            atomic_json_write(record_path, record)

    accepted = sum(status == "accepted" for status in states.values())
    complete = accepted == len(states)
    print(f"[*] SMTP accepted the saved edition for {accepted}/{len(states)} recipients.")
    if complete and not record["ledger_recorded"]:
        for model, filename in record["model_reports"].items():
            data = json.loads((directory / filename).read_text(encoding="utf-8"))
            seen_ledger.mark_delivery_items(str(directory), record["run_date"], model, data)
        record["ledger_recorded"] = True
        atomic_json_write(record_path, record)
    return complete


def send_report_email(run_date_str, html_content, pdf_path, model_name=None, summary_counts=None, is_comparison=False):
    """
    Sends the generated HTML report and PDF attachment via Gmail SMTP.
    """
    # Verify SMTP configurations are present
    if not config.SMTP_SENDER_EMAIL or not config.SMTP_SENDER_PASSWORD:
        print("[!] Warning: SMTP email configurations not set in .env. Skipping email transmission.")
        print("    Please set SMTP_SENDER_EMAIL and SMTP_SENDER_PASSWORD to send email reports.")
        return False
        
    sender = config.SMTP_SENDER_EMAIL
    password = config.SMTP_SENDER_PASSWORD
    recipients = parse_recipients(config.SMTP_RECEIVER_EMAIL)
    if not recipients:
        print("[!] Warning: no SMTP recipients configured. Skipping email transmission.")
        return False
    recipient_header = ", ".join(recipients)
    
    subject = build_email_subject(run_date_str, model_name, summary_counts, is_comparison)
    
    print(f"[*] Preparing email to {recipient_header}...")
    
    # Create Multipart message
    msg = MIMEMultipart("mixed")
    msg["From"] = sender
    msg["To"] = recipient_header
    msg["Subject"] = subject
    
    # Attach HTML body
    msg_alternative = MIMEMultipart("alternative")
    html_part = MIMEText(html_content, "html", "utf-8")
    msg_alternative.attach(html_part)
    msg.attach(msg_alternative)
    
    # Attach PDF if path is valid and exists
    if pdf_path and os.path.exists(pdf_path):
        print(f"[*] Attaching PDF report: {pdf_path}...")
        try:
            with open(pdf_path, "rb") as f:
                pdf_part = MIMEBase("application", "pdf")
                pdf_part.set_payload(f.read())
                
            encoders.encode_base64(pdf_part)
            pdf_basename = os.path.basename(pdf_path)
            pdf_part.add_header(
                "Content-Disposition",
                f"attachment; filename={pdf_basename}"
            )
            msg.attach(pdf_part)
        except Exception as e:
            print(f"[-] Failed to read or attach PDF: {e}")
            return False
    else:
        print(f"[!] Warning: PDF attachment not found at path {pdf_path}.")
        
    # Send email via Gmail SMTP
    try:
        print("[*] Connecting to Gmail SMTP server (smtp.gmail.com:587)...")
        # Gmail SMTP typically uses TLS on port 587
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=SMTP_TIMEOUT)
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        
        print("[*] Logging in to SMTP server...")
        server.login(sender, password)
        
        print(f"[*] Dispatching email to {recipient_header}...")
        refused = server.sendmail(sender, recipients, msg.as_string())
        server.quit()
        if refused:
            print(f"[!] SMTP refused {len(refused)} of {len(recipients)} recipients.")
            return False
        print("[+] Email sent successfully!")
        return True
        
    except Exception as e:
        print(f"[-] SMTP Email transmission failed: {e}")
        print("    Ensure you are using a 16-character Gmail App Password if 2-Step Verification is enabled.")
        return False

if __name__ == "__main__":
    # Small test setup
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    config.SMTP_SENDER_EMAIL = os.getenv("SMTP_SENDER_EMAIL")
    config.SMTP_SENDER_PASSWORD = os.getenv("SMTP_SENDER_PASSWORD")
    config.SMTP_RECEIVER_EMAIL = os.getenv("SMTP_RECEIVER_EMAIL", ", ".join(config.DEFAULT_SMTP_RECEIVER_EMAILS))
    
    # Dummy email test
    test_html = "<h1>Test report</h1><p>If you see this, email sending works.</p>"
    send_report_email("TEST-DATE", test_html, None)
