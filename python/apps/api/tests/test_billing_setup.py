from pathlib import Path

import pytest
from tsp_api.billing import build_payments, plans_path

GSTIN = "29AAAPL1234C1ZF"  # not checksum-valid on purpose; see the invalid-GSTIN test
BASE = {"RAZORPAY_KEY_ID": "k", "RAZORPAY_KEY_SECRET": "s", "RAZORPAY_WEBHOOK_SECRET": "w"}


def test_no_gateway_means_no_payments():
    assert build_payments({}) is None
    assert build_payments({"RAZORPAY_KEY_ID": "k"}) is None  # half-configured is not configured


def test_razorpay_only_without_seller_has_no_invoicer():
    p = build_payments(BASE)
    assert p is not None and p.default_provider == "razorpay" and p.invoicer is None


def test_stripe_can_be_added_and_chosen_as_default():
    p = build_payments(
        {
            **BASE,
            "STRIPE_SECRET_KEY": "sk",
            "STRIPE_WEBHOOK_SECRET": "w",
            "DEFAULT_PAYMENT_PROVIDER": "stripe",
        }
    )
    assert set(p.providers) == {"razorpay", "stripe"} and p.default_provider == "stripe"


def test_a_complete_seller_enables_invoicing_and_a_bad_gstin_fails_fast():
    from tests_support import valid_gstin_for

    env = {
        **BASE,
        "SELLER_LEGAL_NAME": "Acme",
        "SELLER_GSTIN": valid_gstin_for("29"),
        "SELLER_ADDRESS": "x",
        "SELLER_STATE_CODE": "29",
        "SELLER_SAC": "998314",
        "SELLER_GST_RATE": "18",
    }
    p = build_payments(env)
    assert p.invoicer is not None and str(p.invoicer.seller.gst_rate) == "18"
    with pytest.raises(ValueError, match="GSTIN"):
        build_payments({**env, "SELLER_GSTIN": GSTIN})


def test_shipped_plans_file_is_valid():
    from tsp.payments import load_plans

    path = Path(__file__).parents[1] / "plans.toml"
    plans = load_plans(path)
    assert {p.code for p in plans} == {"pro_monthly", "credits_1000"}
    assert (
        plans_path({"TSP_PLANS_FILE": str(path)}) == path
        and plans_path({"TSP_PLANS_FILE": "/nope"}) is None
    )
