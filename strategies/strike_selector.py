"""Index options strike selection engine for NIFTY and SENSEX options buying."""

from typing import Optional
from market_data.models import OptionChain, OptionQuote, OptionType
from strategies.models import StrikePreference


class StrikeSelector:
    """Selects the exact option contract based on index spot, direction, and preference."""

    @staticmethod
    def select_strike(
        chain: OptionChain,
        direction: str,  # "CE" or "PE"
        preference: StrikePreference = StrikePreference.ATM,
    ) -> Optional[OptionQuote]:
        """Selects the optimal option quote matching the requested strike preference.

        Args:
            chain: Complete OptionChain with calls and puts
            direction: "CE" for Call or "PE" for Put (strictly buying)
            preference: ATM, ITM_1, ITM_2, OTM_1, OTM_2

        Returns:
            OptionQuote if match found, else None
        """
        if not chain.strikes:
            return None

        # Sorted strikes ascending
        sorted_strikes = sorted(chain.strikes)
        atm_strike = chain.get_atm_strike()

        try:
            atm_index = sorted_strikes.index(atm_strike)
        except ValueError:
            # Pick closest
            atm_strike = min(sorted_strikes, key=lambda s: abs(s - chain.spot_price))
            atm_index = sorted_strikes.index(atm_strike)

        target_index = atm_index

        if direction == "CE":
            # For Calls: Lower strikes are ITM, Higher strikes are OTM
            if preference == StrikePreference.ATM:
                target_index = atm_index
            elif preference == StrikePreference.ITM_1:
                target_index = max(0, atm_index - 1)
            elif preference == StrikePreference.ITM_2:
                target_index = max(0, atm_index - 2)
            elif preference == StrikePreference.OTM_1:
                target_index = min(len(sorted_strikes) - 1, atm_index + 1)
            elif preference == StrikePreference.OTM_2:
                target_index = min(len(sorted_strikes) - 1, atm_index + 2)

            target_strike = sorted_strikes[target_index]
            strike_key = str(int(target_strike))
            return chain.calls.get(strike_key)

        elif direction == "PE":
            # For Puts: Higher strikes are ITM, Lower strikes are OTM
            if preference == StrikePreference.ATM:
                target_index = atm_index
            elif preference == StrikePreference.ITM_1:
                target_index = min(len(sorted_strikes) - 1, atm_index + 1)
            elif preference == StrikePreference.ITM_2:
                target_index = min(len(sorted_strikes) - 1, atm_index + 2)
            elif preference == StrikePreference.OTM_1:
                target_index = max(0, atm_index - 1)
            elif preference == StrikePreference.OTM_2:
                target_index = max(0, atm_index - 2)

            target_strike = sorted_strikes[target_index]
            strike_key = str(int(target_strike))
            return chain.puts.get(strike_key)

        return None
