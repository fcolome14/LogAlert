import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

webhook_url = os.getenv("WEBHOOK_URL")

payload = {
    "@type": "MessageCard",
    "@context": "http://schema.org/extensions",
    "summary": "Summary of the message",
    "themeColor": "0076D7",
    "title": "FECS TEST TITLE",
    "text": "FECS TEST BODY 👋"
}

response = requests.post(
    webhook_url,
    data=json.dumps(payload),
    headers={"Content-Type": "application/json"},
    verify=False
)

print(f"Status: {response.status_code}")