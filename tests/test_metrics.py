import numpy as np
import pytest

from nfip.metrics import credibility_z, decile_table, gamma_deviance, gini, lorenz_curve, normalized_gini, top_decile_lift


def test_perfect_ranking_has_normalized_gini_one():
    y = np.array([1.0, 5.0, 2.0, 10.0, 3.0])
    assert normalized_gini(y, y) == pytest.approx(1.0)


def test_reversed_ranking_is_negative_and_constant_prediction_near_zero():
    y = np.arange(1, 101, dtype=float)
    assert normalized_gini(y, -y) == pytest.approx(-1.0)
    rng = np.random.default_rng(0)
    y_big = rng.gamma(2.0, 1000.0, 50_000)
    assert abs(normalized_gini(y_big, rng.random(50_000))) < 0.02


def test_gini_matches_hand_computation():
    # order by pred desc -> actual [4, 3, 2, 1]; cum share [.4, .7, .9, 1.0]
    actual = np.array([1.0, 2.0, 3.0, 4.0])
    pred = np.array([0.1, 0.2, 0.3, 0.4])
    expected = (0.4 + 0.7 + 0.9 + 1.0) / 4 - 5 / 8
    assert gini(actual, pred) == pytest.approx(expected)


def test_gini_shape_mismatch_raises():
    with pytest.raises(ValueError):
        gini(np.ones(3), np.ones(4))


def test_lorenz_curve_endpoints():
    x, y = lorenz_curve(np.array([1.0, 2.0, 3.0]), np.array([3.0, 2.0, 1.0]))
    assert x[0] == 0 and x[-1] == 1
    assert y[0] == 0 and y[-1] == pytest.approx(1.0)
    assert np.all(np.diff(y) >= 0)


def test_decile_table_and_lift():
    rng = np.random.default_rng(1)
    pred = rng.gamma(2.0, 1.0, 10_000)
    actual = pred * rng.gamma(10.0, 0.1, 10_000)  # well calibrated, noisy
    t = decile_table(actual, pred)
    assert len(t) == 10 and t["n"].sum() == 10_000
    assert t["mean_pred"].is_monotonic_increasing
    assert t["actual_to_expected"].between(0.85, 1.15).all()
    assert top_decile_lift(actual, pred) > 2


def test_gamma_deviance_zero_for_exact_and_positive_otherwise():
    y = np.array([10.0, 20.0, 30.0])
    assert gamma_deviance(y, y) == pytest.approx(0.0)
    assert gamma_deviance(y, y * 1.5) > 0
    with pytest.raises(ValueError):
        gamma_deviance(np.array([0.0, 1.0]), np.array([1.0, 1.0]))


def test_credibility_square_root_rule():
    assert credibility_z(1082) == pytest.approx(1.0)
    assert credibility_z(5000) == pytest.approx(1.0)
    assert credibility_z(270.5) == pytest.approx(0.5)
    assert credibility_z(0) == 0


def test_gini_ignores_order_of_tied_predictions():
    actual = np.array([1.0, 9.0, 5.0, 5.0])
    pred = np.array([1.0, 1.0, 2.0, 2.0])
    reordered = [1, 0, 3, 2]
    assert gini(actual, pred) == pytest.approx(gini(actual[reordered], pred[reordered]))
    # a constant prediction carries no ranking information at all
    assert normalized_gini(actual, np.ones(4)) == pytest.approx(0.0)
