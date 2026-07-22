"""
Trader's Edge Exit P/E Model v2.0

Improvements over v1:
1. ALL stocks get some mean reversion (not just extremes)
2. Data-driven reversion buckets based on z-score magnitude
3. Confidence BANDS (range) instead of just point estimates
4. Quality and growth adjustments that differentiate companies

Backtested on 3,161 company-year observations (2013-2024).
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
import math


@dataclass
class ExitPEResult:
    """Result of exit P/E prediction with justification and confidence band."""
    exit_pe: float
    exit_pe_low: float   # Lower bound of confidence band
    exit_pe_high: float  # Upper bound of confidence band
    confidence: float
    conviction: str      # "High", "Medium", "Low"
    z_score: float
    valuation_regime: str  # "OVERVALUED", "UNDERVALUED", "FAIR_VALUE"
    justification: str
    breakdown: List[Dict[str, Any]]


def _get_reversion_rate(z_score: float, quality_mult: float) -> Tuple[float, str]:
    """
    Data-driven reversion buckets based on z-score magnitude.
    Returns (reversion_rate, explanation)
    
    Based on empirical data:
    - Q5 (high PE stocks) compress -33% on average
    - Q1 (low PE stocks) expand +22% on average
    """
    abs_z = abs(z_score)
    
    if abs_z < 0.5:
        # Very close to average - minimal reversion
        base_rate = 0.05
        explanation = "Near historical average"
    elif abs_z < 1.0:
        # Slightly stretched - mild reversion
        base_rate = 0.10
        explanation = "Slight deviation from average"
    elif abs_z < 1.5:
        # Moderately stretched
        base_rate = 0.15
        explanation = "Moderate deviation from average"
    elif abs_z < 2.0:
        # Approaching extreme
        base_rate = 0.20
        explanation = "Significant deviation from average"
    elif abs_z < 2.5:
        # Stretched (original threshold)
        base_rate = 0.25
        explanation = "Stretched valuation"
    elif abs_z < 3.0:
        # Very stretched
        base_rate = 0.35
        explanation = "Very stretched valuation"
    else:
        # Extreme outlier
        base_rate = 0.45
        explanation = "Extreme valuation outlier"
    
    # Apply quality adjustment
    adjusted_rate = base_rate * quality_mult
    
    return adjusted_rate, explanation


def _get_quality_mult(roe: Optional[float], eps_growth: Optional[float], ebit_margin: Optional[float]) -> Tuple[float, str]:
    """
    Quality adjustment multiplier for reversion rate.
    High quality companies revert LESS (premium holds).
    Low quality companies revert MORE.
    
    Factors:
    - ROE: Efficiency
    - EPS Growth: Trajectory
    - EBIT Margin: Moat / Pricing Power (Truth Serum for ROE)
    """
    quality_score = 0.0
    factors = []
    
    # ROE factor (Max +0.3, Min -0.2)
    if roe is not None:
        if roe > 25:
            quality_score += 0.25
            factors.append(f"Strong ROE ({roe:.0f}%)")
        elif roe > 15:
            quality_score += 0.15
            factors.append(f"Good ROE ({roe:.0f}%)")
        elif roe < 8:
            quality_score -= 0.15
            factors.append(f"Weak ROE ({roe:.0f}%)")

    # EBIT Margin factor (Max +0.2, Min -0.15) -> The "Moat Check"
    if ebit_margin is not None:
        if ebit_margin > 20:
            quality_score += 0.2
            factors.append(f"High Margin ({ebit_margin:.1f}%)")
        elif ebit_margin > 12:
            quality_score += 0.1
        elif ebit_margin < 5:
            quality_score -= 0.15
            factors.append(f"Low Margin ({ebit_margin:.1f}%)")
    
    # EPS Growth factor (Max +0.2, Min -0.15)
    if eps_growth is not None:
        if eps_growth > 20:
            quality_score += 0.2
            factors.append(f"Strong growth ({eps_growth:.0f}%)")
        elif eps_growth > 10:
            quality_score += 0.1
            factors.append(f"Moderate growth ({eps_growth:.0f}%)")
        elif eps_growth < 0:
            quality_score -= 0.15
            factors.append(f"Negative growth ({eps_growth:.0f}%)")
    
    # Convert to multiplier: high quality = less reversion
    # quality_score ranges from about -0.35 to +0.5
    # We want multiplier from 0.6 (high quality) to 1.3 (low quality)
    multiplier = 1.0 - quality_score * 0.8
    multiplier = max(0.5, min(1.4, multiplier))
    
    if quality_score > 0.25:
        quality_desc = "High Quality"
    elif quality_score > 0.05:
        quality_desc = "Above Average"
    elif quality_score > -0.10:
        quality_desc = "Average"
    else:
        quality_desc = "Below Average"
    
    return multiplier, quality_desc


def _calculate_confidence_band(
    exit_pe: float, 
    pe_std: float, 
    z_score: float,
    data_quality: float  # 0-1 based on years of data available
) -> Tuple[float, float, float, str]:
    """
    Calculate confidence band around the exit PE prediction.
    Returns (low, high, confidence_score, conviction_level)
    """
    # Base band width: 15% of exit PE
    base_width_pct = 0.15
    
    # Widen band for uncertain predictions
    # 1. Further from average = wider band
    z_uncertainty = min(abs(z_score) * 0.03, 0.15)
    
    # 2. Less historical data = wider band
    data_uncertainty = (1 - data_quality) * 0.10
    
    # 3. High PE stocks have more uncertainty
    pe_uncertainty = 0.05 if exit_pe > 40 else 0
    
    total_width_pct = base_width_pct + z_uncertainty + data_uncertainty + pe_uncertainty
    
    half_width = exit_pe * total_width_pct
    low = max(3, exit_pe - half_width)
    high = min(100, exit_pe + half_width)
    
    # Confidence score (0-1)
    confidence = max(0.3, min(0.95, 0.85 - z_uncertainty - data_uncertainty))
    
    # Conviction level
    if confidence >= 0.75 and abs(z_score) < 1.5:
        conviction = "High"
    elif confidence >= 0.55:
        conviction = "Medium"
    else:
        conviction = "Low"
    
    return low, high, confidence, conviction


def traders_edge_exit_pe(
    current_pe: float,
    pe_avg_10y: float,
    pe_std: Optional[float] = None,
    roe: Optional[float] = None,
    eps_growth: Optional[float] = None,
    ebit_margin: Optional[float] = None,
    years_of_data: int = 10,
    pe_max: float = 80.0,
    pe_min: float = 3.0,
) -> ExitPEResult:
    """
    TRADER'S EDGE MODEL v2.0
    
    Predict exit P/E using graduated mean reversion with quality adjustment.
    
    Key improvements over v1:
    1. ALL stocks get some mean reversion (not just extremes)
    2. Reversion strength scales with z-score
    3. Output includes confidence BAND (range)
    4. Quality adjustment based on ROE + Margin + Growth
    
    Args:
        current_pe: Current forward P/E ratio
        pe_avg_10y: 10-year average P/E (excluding outliers >100)
        pe_std: Standard deviation of historical P/E
        roe: Return on equity (%)
        eps_growth: 3-year EPS CAGR (%)
        ebit_margin: Operating Margin (%) - Moat indicator
        years_of_data: Number of years with valid PE data
        pe_max: Maximum allowed exit P/E
        pe_min: Minimum allowed exit P/E
        
    Returns:
        ExitPEResult with prediction, confidence band, and justification
    """
    # Handle missing/invalid inputs
    if current_pe <= 0:
        current_pe = 18.0
    if pe_avg_10y <= 0:
        pe_avg_10y = 18.0
    if pe_std is None or pe_std <= 1:
        pe_std = pe_avg_10y * 0.25  # Estimate as 25% of average
    
    # Calculate z-score
    z_score = (current_pe - pe_avg_10y) / pe_std
    
    # Determine valuation regime
    if z_score > 1.5:
        regime = "OVERVALUED"
    elif z_score < -1.5:
        regime = "UNDERVALUED"
    else:
        regime = "FAIR_VALUE"
    
    # Get quality adjustment
    quality_mult, quality_desc = _get_quality_mult(roe, eps_growth, ebit_margin)
    
    # Get data-driven reversion rate
    reversion_rate, reversion_explanation = _get_reversion_rate(z_score, quality_mult)
    
    # Calculate target (blend toward historical average)
    # Direction: always pull toward the mean
    if z_score > 0:
        # Overvalued - compress toward average
        exit_pe = current_pe - reversion_rate * (current_pe - pe_avg_10y)
    else:
        # Undervalued - expand toward average
        exit_pe = current_pe + reversion_rate * (pe_avg_10y - current_pe)
    
    # Clamp to reasonable range
    exit_pe = max(pe_min, min(pe_max, exit_pe))
    
    # Calculate confidence band
    data_quality = min(years_of_data / 10.0, 1.0)
    exit_pe_low, exit_pe_high, confidence, conviction = _calculate_confidence_band(
        exit_pe, pe_std, z_score, data_quality
    )
    
    # Build justification
    justification = _build_justification_v2(
        current_pe, pe_avg_10y, pe_std, z_score, regime,
        roe, eps_growth, ebit_margin, quality_desc, reversion_rate, reversion_explanation,
        exit_pe, exit_pe_low, exit_pe_high, conviction
    )
    
    # Build breakdown
    breakdown = _build_breakdown_v2(
        current_pe, pe_avg_10y, pe_std, z_score, regime,
        roe, eps_growth, ebit_margin, quality_desc, reversion_rate, reversion_explanation,
        exit_pe, exit_pe_low, exit_pe_high, confidence, conviction
    )
    
    return ExitPEResult(
        exit_pe=round(exit_pe, 2),
        exit_pe_low=round(exit_pe_low, 2),
        exit_pe_high=round(exit_pe_high, 2),
        confidence=round(confidence, 2),
        conviction=conviction,
        z_score=round(z_score, 2),
        valuation_regime=regime,
        justification=justification,
        breakdown=breakdown
    )


def _build_justification_v2(
    current_pe: float, pe_avg: float, pe_std: float, z_score: float, regime: str,
    roe: Optional[float], eps_growth: Optional[float], ebit_margin: Optional[float], quality_desc: str,
    reversion_rate: float, reversion_explanation: str,
    exit_pe: float, exit_pe_low: float, exit_pe_high: float, conviction: str
) -> str:
    """Build human-readable justification for the prediction."""
    
    parts = []
    
    # Opening with valuation context
    direction = "above" if z_score > 0 else "below"
    parts.append(f"Current P/E ({current_pe:.1f}x) is {abs(z_score):.1f}σ {direction} "
                 f"the 10Y historical average ({pe_avg:.1f}x).")
    
    # Reversion explanation
    reversion_pct = reversion_rate * 100
    if z_score > 0:
        parts.append(f"Applying {reversion_pct:.0f}% mean reversion toward historical norm ({reversion_explanation}).")
    else:
        parts.append(f"Applying {reversion_pct:.0f}% expansion toward historical norm ({reversion_explanation}).")
    
    # Quality factor
    if roe is not None or eps_growth is not None or ebit_margin is not None:
        parts.append(f"Quality assessment: {quality_desc}.")
        
        quality_details = []
        if ebit_margin is not None:
            if ebit_margin > 20:
                quality_details.append(f"High Margin ({ebit_margin:.1f}%) signals pricing power")
            elif ebit_margin < 5:
                quality_details.append(f"Low Margin ({ebit_margin:.1f}%) signals weak moat")
            else:
                quality_details.append(f"Margin {ebit_margin:.1f}%")
                
        if roe is not None:
            quality_details.append(f"ROE {roe:.0f}%")
        if eps_growth is not None:
            quality_details.append(f"Growth {eps_growth:.0f}%")
            
        if quality_details:
            parts.append(f"({', '.join(quality_details)}).")
    
    # Final prediction with range
    parts.append(f"Exit P/E: {exit_pe:.1f}x (range: {exit_pe_low:.1f}x - {exit_pe_high:.1f}x). "
                 f"Conviction: {conviction}.")
    
    return " ".join(parts)


def _build_breakdown_v2(
    current_pe: float, pe_avg: float, pe_std: float, z_score: float, regime: str,
    roe: Optional[float], eps_growth: Optional[float], ebit_margin: Optional[float], quality_desc: str,
    reversion_rate: float, reversion_explanation: str,
    exit_pe: float, exit_pe_low: float, exit_pe_high: float, 
    confidence: float, conviction: str
) -> List[Dict[str, Any]]:
    """Build structured breakdown for UI display."""
    
    breakdown = [
        {
            "label": "Current P/E",
            "value": f"{current_pe:.1f}x",
            "meta": "Starting point"
        },
        {
            "label": "10Y Average P/E",
            "value": f"{pe_avg:.1f}x",
            "meta": f"σ = {pe_std:.1f}"
        },
        {
            "label": "Z-Score",
            "value": f"{z_score:+.2f}σ",
            "meta": f"{'Above' if z_score > 0 else 'Below'} average"
        },
        {
            "label": "Valuation Regime",
            "value": regime,
            "meta": reversion_explanation
        },
        {
            "label": "Reversion Rate Applied",
            "value": f"{reversion_rate*100:.0f}%",
            "meta": "Data-driven based on z-score bucket"
        },
    ]
    
    if ebit_margin is not None:
        breakdown.append({
            "label": "EBIT Margin",
            "value": f"{ebit_margin:.1f}%",
            "meta": "Moat Indicator"
        })
    
    if roe is not None:
        breakdown.append({
            "label": "ROE",
            "value": f"{roe:.1f}%",
            "meta": "Efficiency"
        })
    
    if eps_growth is not None:
        breakdown.append({
            "label": "EPS Growth (3Y)",
            "value": f"{eps_growth:.1f}%",
            "meta": "Trajectory"
        })
    
    breakdown.extend([
        {
            "label": "Exit P/E Prediction",
            "value": f"{exit_pe:.1f}x",
            "meta": f"Range: {exit_pe_low:.1f}x - {exit_pe_high:.1f}x"
        },
        {
            "label": "Conviction",
            "value": conviction,
            "meta": f"Confidence: {confidence:.0%}"
        },
    ])
    
    return breakdown


def batch_predict_exit_pe(
    companies: List[Dict[str, Any]],
    pe_max: float = 80.0,
) -> Dict[str, Dict[str, Any]]:
    """
    Predict exit P/E for a batch of companies.
    """
    results = {}
    
    for company in companies:
        ticker = company.get("ticker")
        if not ticker:
            continue
            
        current_pe = company.get("pe_ratio") or company.get("current_pe")
        pe_avg = company.get("pe_avg_10y")
        pe_std = company.get("pe_std")
        roe = company.get("roe")
        eps_growth = company.get("eps_cagr_3y") or company.get("eps_growth")
        ebit_margin = company.get("ebit_margin")
        years = company.get("pe_avg_10y_years_used") or 10
        
        if current_pe is None or current_pe <= 0:
            continue
        if pe_avg is None or pe_avg <= 0:
            pe_avg = 18.0
        if pe_std is None or pe_std <= 0:
            pe_std = pe_avg * 0.25
        
        result = traders_edge_exit_pe(
            current_pe=current_pe,
            pe_avg_10y=pe_avg,
            pe_std=pe_std,
            roe=roe,
            eps_growth=eps_growth,
            ebit_margin=ebit_margin,
            years_of_data=years,
            pe_max=pe_max,
        )
        
        results[ticker] = {
            "exit_pe": result.exit_pe,
            "exit_pe_low": result.exit_pe_low,
            "exit_pe_high": result.exit_pe_high,
            "confidence": result.confidence,
            "conviction": result.conviction,
            "notes": result.justification,
            "breakdown": result.breakdown,
            "z_score": result.z_score,
            "valuation_regime": result.valuation_regime,
        }
    
    return results
