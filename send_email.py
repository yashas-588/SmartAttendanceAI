import smtplib
from email.mime.text import MIMEText

def send_email(to_email, student_name):
    sender_email = "yashasr588@gmail.com"
    app_password = "ctbybegoqwktyinf"  # remove spaces

    subject = "Attendance Alert"
    body = f"{student_name} was absent today."

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, app_password)
            server.send_message(msg)

        print(f"✅ Email sent to {to_email}")

    except Exception as e:
        print("❌ Email error:", e)