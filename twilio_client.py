"""Twilio client wrapper"""
import os
import logging

logger = logging.getLogger("twilio-client")


class TwilioClient:
    def __init__(self):
        self.account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self.auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        self.from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
        self._client = None

    def get_client(self):
        if not self._client and self.account_sid:
            from twilio.rest import Client
            self._client = Client(self.account_sid, self.auth_token)
        return self._client
