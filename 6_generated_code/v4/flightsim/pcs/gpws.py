"""Ground-proximity condition. [a: Behaviour B15] [a: L2SYS-REQ-02.04]
[a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#updateAttentionGetters]"""

TIME_TO_IMPACT_S = 10.0   # [a: Behaviour B15] model silent
AGL_FLOOR_M = 15.0        # [c] B15 names a floor without a value; settled by a model attribute
DESCENT_EPS_MPS = 0.5     # [c] "descending" threshold against numerical noise
FLASH_HZ = 2.0            # [a: Behaviour B15] about 2 Hz


def warning(agl_m, v_speed_mps, on_ground, park_brake):
    if on_ground and park_brake:
        return False  # inhibited [a: Behaviour B15]
    if v_speed_mps >= -DESCENT_EPS_MPS:
        return False
    descent = -v_speed_mps
    return (agl_m / descent) < TIME_TO_IMPACT_S or agl_m < AGL_FLOOR_M
