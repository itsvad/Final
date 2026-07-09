from orb_bot.position_sizer import calculate_position_size


def test_example_from_spec_600_risk_400_per_contract_gives_1():
    # $600 risk, $400 risk per contract -> 1 contract (2 would be $800, over budget)
    result = calculate_position_size(
        stop_distance_ticks=16, tick_value=25.0, risk_amount_usd=600.0
    )  # 16 ticks * $25 = $400/contract
    assert result.contracts == 1
    assert result.risk_per_contract_usd == 400.0
    assert result.total_risk_usd == 400.0
    assert result.tradeable is True


def test_exact_multiple_uses_full_budget():
    result = calculate_position_size(
        stop_distance_ticks=10, tick_value=10.0, risk_amount_usd=1000.0
    )  # $100/contract -> exactly 10 contracts
    assert result.contracts == 10
    assert result.total_risk_usd == 1000.0


def test_risk_too_small_for_one_contract_skips_trade():
    result = calculate_position_size(
        stop_distance_ticks=100, tick_value=50.0, risk_amount_usd=100.0
    )  # $5000/contract, way over $100 budget
    assert result.contracts == 0
    assert result.tradeable is False
    assert result.skipped_reason is not None


def test_max_contracts_cap_is_enforced():
    result = calculate_position_size(
        stop_distance_ticks=1, tick_value=1.0, risk_amount_usd=1_000_000.0, max_contracts=10
    )
    assert result.contracts == 10


def test_min_contracts_threshold():
    result = calculate_position_size(
        stop_distance_ticks=10, tick_value=10.0, risk_amount_usd=150.0, min_contracts=2
    )  # $100/contract, budget covers 1 but min_contracts=2 -> skip
    assert result.contracts == 0
    assert result.tradeable is False


def test_invalid_inputs_are_rejected():
    assert calculate_position_size(0, 10.0, 100.0).tradeable is False
    assert calculate_position_size(10, 0, 100.0).tradeable is False
    assert calculate_position_size(10, 10.0, 0).tradeable is False
