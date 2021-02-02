import typing as t

from dataclasses import dataclass, field, asdict
from te_commons.consent import BaseConsent, ConsentAttribute
from tcf2parser.decorators import cached_property
from tcf2parser.exceptions import TcfParseException


class TracktorConsent(BaseConsent):
    def __init__(self, has_consent="0", **kwargs):
        super().__init__(**kwargs)
        self.has_consent = int(has_consent == "1")

    class Purposes:
        COOKIES = 1
        SIMPLE_TARGETING = 2
        BUILD_PROFILES = 3
        PROFILE_TARGETING = 4

    class Vendors:
        ADA = 788
        IP = 789

    @cached_property
    @ConsentAttribute(needs_cookies=True)
    def cookies(self):
        return (
            self._vendors_allowed
            and self.Purposes.COOKIES in self._purposes_consent
        )

    @cached_property
    @ConsentAttribute(needs_cookies=True)
    def retargeting(self):
        return (
            self._vendors_allowed
            and self._build_profiles
            and self.Purposes.SIMPLE_TARGETING in self._purposes_consent_or_li
            and self.Purposes.PROFILE_TARGETING in self._purposes_consent
        )

    @cached_property
    @ConsentAttribute(needs_cookies=True)
    def user_identifier(self):
        return self._build_profiles

    @cached_property
    @ConsentAttribute(literal=True)
    def track_anonymous(self):
        return True

    @cached_property
    @ConsentAttribute()
    def track_ip_hashed(self):
        return True

    @cached_property
    @ConsentAttribute()
    def track_ip_clear(self):
        return self.Purposes.BUILD_PROFILES in self._purposes_consent

    @cached_property
    def _purposes_consent(self):
        return self.tcf.core.purposes_consent

    @cached_property
    def _purposes_li(self):
        return self.tcf.core.purposes_li

    @cached_property
    def _purposes_consent_or_li(self) -> t.Set[int]:
        return self._purposes_consent | self._purposes_li

    @cached_property
    def _build_profiles(self):
        return (
            self._vendors_allowed
            and self.cookies
            and self.Purposes.BUILD_PROFILES in self._purposes_consent
        )

    @cached_property
    def _vendors_allowed(self) -> bool:
        vendors_allowed = self.tcf.core.vendor_consent | self.tcf.core.vendor_li
        return self.Vendors.IP in vendors_allowed

    _relevant_vendors = {Vendors.ADA, Vendors.IP}

    @dataclass
    class TcfSummary:
        external_consent: int
        string_exists: int = 0
        purposes_consent: t.List[int] = field(default_factory=list)
        purposes_li: t.List[int] = field(default_factory=list)
        vendor_consent: t.List[int] = field(default_factory=list)
        vendor_li: t.List[int] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        if self.tcf is None:
            output = self.TcfSummary(
                external_consent=self.has_consent
            )
        else:
            try:
                output = self.TcfSummary(
                    external_consent=self.has_consent,
                    string_exists=1,
                    purposes_consent=list(self._purposes_consent),
                    purposes_li=list(self._purposes_li),
                    vendor_consent=list(
                        self._relevant_vendors & self.tcf.core.vendor_consent
                    ),
                    vendor_li=list(
                        self._relevant_vendors & self.tcf.core.vendor_li
                    )
                )
            except TcfParseException:
                output = self.TcfSummary(
                    external_consent=self.has_consent,
                    string_exists=-1
                )

        return asdict(output)
