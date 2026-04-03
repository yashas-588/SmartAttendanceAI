import smtplib
from email.mime.text import MIMEText

# 🔥 YOUR CORRECT EMAIL
EMAIL = "yashasr435@gmail.com"
PASSWORD = "vydiaiiqxmdenpxy"

def send_email(to_email, student_name, subject_name):
    try:
        subject = "🚨 Attendance Alert"
        body = f"{student_name} was ABSENT in {subject_name} today."

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL
        msg["To"] = to_email

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL, PASSWORD)

        server.sendmail(EMAIL, to_email, msg.as_string())
        server.quit()

        print(f"📧 Email sent to {to_email}")

    except Exception as e:
        print("❌ Email error:", e)