from decimal import Decimal

import pytest

from opsagent.config import Settings
from opsagent.money import MoneyError, format_inr, rupee_str, to_paise, to_rupees


def test_to_paise_decimals():  # TEST-001b
    assert to_paise(Decimal("450.50")) == 45050
    assert to_paise("1299.99") == 129999
    assert to_paise("6,499.00") == 649900
    assert to_paise(5000) == 500000
    assert to_rupees(45050) == Decimal("450.50")
    assert rupee_str(129999) == "1299.99"
    assert format_inr(Decimal("6499")) == "₹6,499.00"


def test_money_rejects_float_and_extra_places():
    with pytest.raises(MoneyError):
        to_paise(450.5)
    with pytest.raises(MoneyError):
        to_paise("1.005")
    assert to_paise("1.500") == 150  # trailing zero is fine


def test_settings_masks_keys():  # TEST-001
    s = Settings(groq_api_key="gsk_secretvalue123")
    assert "secretvalue" not in repr(s)
    assert "gsk_****" in repr(s)


def test_missing_key_message():
    from opsagent.config import ConfigError

    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        Settings(groq_api_key=None).require_llm()
