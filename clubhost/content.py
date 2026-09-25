from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SiteContent(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    title: str = Field(min_length=3, max_length=100)
    tagline: str = Field(default='', max_length=180)
    description: str = Field(default='', max_length=4000)
    contact_email: str = Field(default='', max_length=254)
    link_label: str = Field(default='', max_length=40)
    link_url: str = Field(default='', max_length=500)
    event_date: str = Field(default='', max_length=40)
    location: str = Field(default='', max_length=180)
    capacity: int = Field(default=30, ge=1, le=10000)
    registration_open: bool = False

    @field_validator('link_url')
    @classmethod
    def valid_link(cls, value):
        from urllib.parse import urlsplit
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Links must be an http or https URL without credentials')
        return value

    @field_validator('contact_email')
    @classmethod
    def valid_email(cls, value):
        return validate_email(value) if value else ''

    @field_validator('event_date')
    @classmethod
    def valid_date(cls, value):
        if value:
            date = datetime.fromisoformat(value)
            if date.tzinfo is None or date.utcoffset() is None:
                raise ValueError('Event date must include a timezone offset')
        return value

    @model_validator(mode='after')
    def registration_details(self):
        if self.registration_open and (not self.event_date or not self.location):
            raise ValueError('Set an event date and location before opening registration')
        return self


def validate_email(value):
    import re
    value = value.strip().casefold()
    if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', value) or len(value) > 254:
        raise ValueError('Enter a valid email address')
    return value


def default_content(name):
    return SiteContent(title=name.replace('-', ' ').title(),
                       tagline='Find your people. Build something together.',
                       description="Welcome to our club. Check back for news and upcoming events.").model_dump()


class SaveContent(BaseModel):
    content: SiteContent
    expected_version: int = Field(ge=1)


class Visibility(BaseModel):
    published: bool


class Registration(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    website: str = Field(default='', max_length=200)  # Honeypot, never stored.

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        return validate_email(value)


class Attendance(BaseModel):
    action: Literal['check-in', 'undo-check-in', 'cancel']
