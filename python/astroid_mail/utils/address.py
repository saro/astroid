"""Port of src/utils/address.cc (the parts Phase 1 needs).

Uses GMime's InternetAddress parser so name/email splitting behaves the
same as the C++ implementation.
"""

from __future__ import annotations

import gi

gi.require_version("GMime", "3.0")
from gi.repository import GMime  # noqa: E402

GMime.init()


class Address:
    def __init__(self, full: str = "", email: str | None = None,
                 name: str | None = None):
        if email is not None:
            self._name = name or ""
            self._email = email
            self.valid = True
            return

        self._name = ""
        self._email = ""
        self.valid = False

        al = GMime.InternetAddressList.parse(None, full)
        if al is not None and al.length() > 0:
            a = al.get_address(0)
            if isinstance(a, GMime.InternetAddressMailbox):
                self._name = a.get_name() or ""
                self._email = a.get_addr() or ""
                self.valid = True

    def email(self) -> str:
        return self._email

    def name(self) -> str:
        return self._name

    def fail_safe_name(self) -> str:
        """Name if set, else the email address (port of Address::fail_safe_name)."""
        return self._name if self._name else self._email

    def full_address(self) -> str:
        if self._name:
            return f"{self._name} <{self._email}>"
        return self._email


class AddressList:
    def __init__(self, raw: str = ""):
        self.addresses: list[Address] = []
        if not raw:
            return
        al = GMime.InternetAddressList.parse(None, raw)
        if al is None:
            return
        for i in range(al.length()):
            a = al.get_address(i)
            if isinstance(a, GMime.InternetAddressMailbox):
                self.addresses.append(
                    Address(email=a.get_addr() or "", name=a.get_name() or ""))
            elif isinstance(a, GMime.InternetAddressGroup):
                members = a.get_members()
                for j in range(members.length()):
                    m = members.get_address(j)
                    if isinstance(m, GMime.InternetAddressMailbox):
                        self.addresses.append(
                            Address(email=m.get_addr() or "",
                                    name=m.get_name() or ""))

    def __len__(self) -> int:
        return len(self.addresses)

    def __iter__(self):
        return iter(self.addresses)
