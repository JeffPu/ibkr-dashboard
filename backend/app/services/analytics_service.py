def simple_return(v_begin: float, v_end: float, net_cash_inflow: float) -> float | None:
    if v_begin == 0:
        return None
    return (v_end - v_begin - net_cash_inflow) / v_begin
