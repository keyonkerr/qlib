"""Alpha158 plus 5 hand-written price-volume factors (path 2: feature engineering).

Referenced from 23_custom_factors.yaml as ``path_exps/custom_handler.py``.
qlib requires ``trusted: true`` next to the class to import a .py module path.
"""

from qlib.contrib.data.handler import Alpha158
from qlib.contrib.data.loader import Alpha158DL

MY_FIELDS = [
    "Corr($close, Log($volume+1), 20)",
    "Std($close, 10)/Mean($close, 10)",
    "Rank($close, 20)",
    "Corr($close, $vwap, 10)",
    "Slope($volume, 20)/Mean($volume, 20)",
]
MY_NAMES = ["MY_CORR_VOL20", "MY_CV10", "MY_RANK20", "MY_CORR_VWAP10", "MY_VOL_TREND20"]


class MyAlpha(Alpha158):
    def get_feature_config(self):
        conf = {
            "kbar": {},
            "price": {"windows": [0], "feature": ["OPEN", "HIGH", "LOW", "VWAP"]},
            "rolling": {},
        }
        fields, names = Alpha158DL.get_feature_config(conf)
        return fields + MY_FIELDS, names + MY_NAMES
