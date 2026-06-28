"""
BookingTools - Appointment availability checking and booking
Stores appointments in a simple JSON file (swap for Cal.com / DB in production)
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("booking-tools")

# Simple file-based store (replace with DB or Cal.com API in production)
APPOINTMENTS_FILE = Path(__file__).parent.parent / "data" / "appointments.json"

# Clinic hours: Mon-Fri 9am-5pm, slots every 30 min
CLINIC_HOURS = {
    "start": 9,   # 9 AM
    "end": 17,    # 5 PM
    "slot_minutes": 30,
}

# Pre-blocked slots for realism
BLOCKED_SLOTS = {
    # format: "YYYY-MM-DD HH:MM"
}


class BookingTools:
    def __init__(self):
        APPOINTMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not APPOINTMENTS_FILE.exists():
            APPOINTMENTS_FILE.write_text("[]")

    def _load_appointments(self) -> list:
        try:
            return json.loads(APPOINTMENTS_FILE.read_text())
        except Exception:
            return []

    def _save_appointments(self, appointments: list):
        APPOINTMENTS_FILE.write_text(json.dumps(appointments, indent=2))

    async def check_availability(self, date: str, time: str, reason: str) -> str:
        """Check if a slot is available"""
        try:
            dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        except ValueError:
            return "I couldn't understand that date/time format. Please provide date as YYYY-MM-DD and time as HH:MM."

        # Check it's a weekday
        if dt.weekday() >= 5:
            return f"I'm sorry, {dt.strftime('%A')} is a weekend and the clinic is closed. We're open Monday through Friday, 9 AM to 5 PM."

        # Check clinic hours
        if dt.hour < CLINIC_HOURS["start"] or dt.hour >= CLINIC_HOURS["end"]:
            return f"That time is outside our clinic hours. We're available Monday to Friday, 9 AM to 5 PM."

        # Check if it's in the past
        if dt < datetime.now():
            return "That date and time has already passed. Please choose a future date."

        # Check for existing bookings
        slot_key = dt.strftime("%Y-%m-%d %H:%M")
        appointments = self._load_appointments()
        for appt in appointments:
            if appt.get("slot") == slot_key and appt.get("status") == "confirmed":
                # Suggest next available
                next_slot = dt + timedelta(minutes=30)
                return (
                    f"I'm sorry, {dt.strftime('%I:%M %p')} on {dt.strftime('%B %d')} is already taken. "
                    f"The next available slot is {next_slot.strftime('%I:%M %p')}. Would that work for you?"
                )

        return (
            f"Great news! {dt.strftime('%I:%M %p')} on {dt.strftime('%A, %B %d')} is available "
            f"for a {reason} appointment. Shall I go ahead and book that for you?"
        )

    async def book_appointment(
        self,
        patient_name: str,
        reason: str,
        date: str,
        time: str,
        phone: str,
    ) -> str:
        """Book an appointment and return confirmation"""
        try:
            dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        except ValueError:
            return "I had trouble with that date/time. Let me try again."

        appointment_id = str(uuid.uuid4())[:8].upper()
        slot_key = dt.strftime("%Y-%m-%d %H:%M")

        appointment = {
            "id": appointment_id,
            "patient_name": patient_name,
            "reason": reason,
            "date": date,
            "time": time,
            "slot": slot_key,
            "phone": phone,
            "status": "confirmed",
            "created_at": datetime.now().isoformat(),
        }

        appointments = self._load_appointments()
        appointments.append(appointment)
        self._save_appointments(appointments)

        logger.info(f"Appointment booked: {appointment_id} for {patient_name}")

        return (
            f"Your appointment is confirmed! Booking reference: {appointment_id}. "
            f"Patient: {patient_name}. "
            f"Date: {dt.strftime('%A, %B %d, %Y')} at {dt.strftime('%I:%M %p')}. "
            f"Reason: {reason}. "
            f"We'll send a reminder to {phone}. "
            f"Is there anything else I can help you with?"
        )
