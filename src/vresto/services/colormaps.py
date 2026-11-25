"""256-entry RGB colour tables for the overlay ramps, so vresto does not need matplotlib.

Each table was sampled once from matplotlib (``cmap(np.linspace(0, 1, 256))[:, :3] * 255``,
cast to uint8) and is stored base64-encoded. ``terrain_land`` is the land part of
"terrain" (0.25 to 1.0) used by the DEM; ``ndvi_lts`` is the brown-to-green ramp
``#6e4e33 #b58b55 #d0b56d #8fb058 #2e7d32`` used by NDVI-LTS. To add a table, sample
it the same way and add its base64 here; ``tests/test_services/test_colormaps.py``
pins every table's checksum.
"""

from __future__ import annotations

import base64
import functools

import numpy as np

_TABLES = {
    "YlGn": (
        "///l/v7j/v7i/v7g/f7f/f7e/f7c/f7b/P7Z/P7Y/P7X/P3V+/3U+/3T+/3R+/3Q+v3O+v3N+v3M+v3K+f3J+f3I+fzG+fzF+PzD+PzC+PzB+Py/9/y+9/y89/y79/y6"
        "9vu49fu49fu39Pq28/q28vq18fm08Pm07/iz7viy7fiy7Pex6/ew6vev6fav6Pau5/Wt5vWt5fWs5fSr5PSr4/Sq4vOp4fOp4PKo3/Kn3vKm3fGm3PGl2/Gk2vCk2fCj"
        "2O+i1++i1e6h1O6g0+2g0eyf0OyezuuezeudzOqcyumcyembyOiaxuiaxeeZw+aYwuaYweWXv+WWvuSWveOVu+OVuuKUuOKTt+GTtuCStOCRs9+Rst+QsN6Pr92Prd2O"
        "rNyNqtyNqduMp9qLpdmLpNmKotiJoNeJn9aIndaHm9WHmtSGmNSFltOFldKEk9GDkdGDkNCCjs+BjM+Bi86Aic1/h8x/hsx+hMt9gsp9gcl8f8l7fch7fMd6esd5eMZ5"
        "d8V4dcR3c8N2ccN1cMJ1bsF0bMBza79yab5xZ71wZb1vZLxuYrtuYLptXrlsXbhrW7hqWbdpWLZoVrVnVLRmUrNmUbJlT7JkTbFjS7BiSq9hSK5gRq1fRa1fQ6xeQatd"
        "QKpcP6lbPqdaPaZaPKVZO6RYOqJXOaFWOKBVN59VN55UNpxTNZtSNJpRM5lRMpdQMZZPMJVOL5RNLpJMLZFMLJBLK49KKo5JKYxIKItIJ4pHJ4lGJodFJYZEJIVEI4RD"
        "IoNCIYJCH4FBHoBBHX9BHH5AG35AGn1AGXw/GHs/F3o+Fnk+FXg+E3c9Enc9EXY9EHU8D3Q8DnM7DXI7DHE7C3A6CnA6CG86B245Bm05BWw4BGs4A2o4Amk3AWg3AGg3"
        "AGc2AGU2AGQ1AGM1AGI0AGE0AGAzAF8zAF4zAF0yAFwyAFoxAFkxAFgwAFcwAFYwAFUvAFQvAFMuAFIuAFEtAE8tAE4sAE0sAEwsAEsrAEorAEkqAEgqAEcpAEYpAEUp"
    ),
    "YlGnBu": (
        "///Z/v7X/f7W/f7V/P7T/P3S+/3R+/3Q+v3O+f3N+fzM+PzL+PzJ9/zI9/vH9vvG9fvE9fvD9PvC9PrB8/q/8/q+8vq98vm88fm68Pm58Pm47/m37/i17vi07viz7fiy"
        "7Pex6/ex6vex6fax6Pax5vWx5fWx5PSx4/Sx4vOx4POx3/Ky3vKy3fGy3PGy2vCy2fCy2O+y1++y1u+y1e6y0+6y0u2z0e2z0Oyzz+yzzeuzzOuzy+qzyuqzyemzx+mz"
        "xui0xOe0wee0v+a0veW0u+S1uOO1tuK1tOG1suC2r9+2rd+2q962qd22pty3pNu3otq3oNm3ndi4m9i4mde4l9a4lNW4ktS5kNO5jdK5i9G5idG5h9C6hM+6gs66gM26"
        "fsy7fMy7esu7eMq7dsm8dMm8csi8cMe9bsa9bMa9asW9aMS+ZsS+ZMO+Y8K/YcG/X8G/XcC/W7/AWb/AV77AVb3BU7zBUbzBT7vBTbrCS7nCSbnCR7jDRbfDQ7fDQbbD"
        "QLXDP7TDPrPDPbHDO7DDOq/DOa7DOK3DN6zCNqrCNanCNKjCMqfCMabCMKXCL6TCLqLBLaHBLKDBKp/BKZ7BKJ3BJ5vBJprBJZnAJJjAI5fAIZbAIJTAH5PAHpLAHZHA"
        "HZC/HY6+HYy+HYu9HYm8HYi7Hoa7HoS6HoO5HoG4HoC4Hn63Hny2H3u1H3m0H3i0H3azH3SyH3OxIHGxIHCwIG6vIGyuIGuuIGmtIWisIWarIWSrIWOqIWGpIWCoIV6o"
        "Il2nIlumIlqmIlmlIlelIlakIlWjIlOjIlKiIlGhI0+hI06gI02gI0ufI0qeI0meI0edI0acI0WcI0ObI0KaJEGaJECZJD6ZJD2YJDyXJDqXJDmWJDiVJDaVJDWUJDSU"
        "JDOSIzKQIjGOITGMIDCKHy+IHi+HHS6FHC2DHCyBGyx/Git9GSp7GCl5Fyl4Fih2FSd0FCdyEyZwEiVuEiRsESRqECNoDyJnDiJlDSFjDCBhCx9fCh9dCR5bCB1ZCB1Y"
    ),
    "YlOrRd": (
        "///M//7K//3J//3H//zG//zF//vD//vC//rA//m///m+//i8//i7//e6//e4//a3//W1//W0//Sz//Sx//Ow//Ov//Kt//Ks//Gq//Cp//Co/++m/++l/+6j/+6i/+2h"
        "/uyf/uye/uud/uub/uqa/umZ/umX/uiW/ueV/ueT/uaS/uaR/uWQ/uSO/uSN/uOM/uKK/uKJ/uGI/uGG/uCF/t+E/t+C/t6B/t2A/t1+/tx9/tt8/tt6/tp5/tp4/tl2"
        "/th1/td0/tZz/tVx/tNw/tJv/tFt/tBs/s5r/s1p/sxo/stn/spl/shk/sdj/sZh/sVg/sNf/sJd/sFc/sBb/r9a/r1Y/rxX/rtW/rpU/rhT/rdS/rZQ/rVP/rNO/rJM"
        "/bFL/bBL/a9K/a5K/axJ/atJ/apI/alI/ahH/adH/aVG/aRG/aNF/aJF/aFE/aBE/Z5D/Z1D/ZxC/ZtC/ZpB/ZlB/ZhA/ZZA/ZU//ZQ//ZM+/ZI+/ZE9/Y89/Y48/Y08"
        "/Iw7/Io7/Ig6/IY6/IQ5/II4/IA4/H43/Hw3/Ho2/Hg2/HY1/HQ0/HI0/HAz/G4z/Gwy/Goy/Ggx/GYw/GQw/GIv/GAv/F4u/Fwu/Fot/Fgt/FYs/FQr/FIr/FAq/E4q"
        "+0wp+ksp+Uko+Ugo+EYn90Qn9kMn9kEm9T8m9D4l8zwl8jsk8jkk8Tck8DYj7zQj7jIi7jEi7S8h7C0h6ywg6yog6ikg6Scf6CUf5yQe5yIe5iAd5R8d5B0c4xwc4xoc"
        "4hkc4Bgc3xcc3hYd3RYd3BUd2hQe2RMe2BIe1xIf1hEf1BAf0w8g0g4g0Q0g0A0gzwwhzQshzAohywkiygkiyQgixwcjxgYjxQUjxAQkwwQkwQMkwAIlvwElvgAlvQAl"
        "uwAmuQAmtwAmtQAmswAmsQAmrwAmrQAmrAAmqgAmqAAmpgAmpAAmogAmoAAmngAmnAAmmgAmmAAmlgAmlQAmkwAmkQAmjwAmjQAmiwAmiQAmhwAmhQAmgwAmgQAmgAAm"
    ),
    "RdYlBu_r": (
        "MTaVMTiWMjqXMz2YND+ZNEKbNUScNkedN0meOEyfOE6hOVGiOlOjO1akO1imPFunPV2oPmCpP2KqP2SsQGetQWmuQmyvQ26wQ3GyRHOzRXa0R3i1SXq2S3y3TX65T4G6"
        "UIO7UoW8VIe9Vom+WIy/Wo7BXJDCXZLDX5TEYZfFY5nGZZvHZ53JaJ/KaqLLbKTMbqbNcKjOcqrPdK3Rdq7ReLDSerLTfLPUfrXVgLfWg7nXhbrYh7zZib7ai7/bjcHc"
        "kMPdksXelMbflsjgmMrhmszhnM3in8/jodHko9LlpdTmp9bnqdjorNnprtrpsNvqstzrtN3rtt7suN/suuDtvOHuvuLuwOPvwuTvxOXwx+bwyefxy+jyzenyz+rz0evz"
        "0+z01e311+712e/22/D23fH34PP34fP14vPz4/Tx5PTv5vXs5/Xq6Pbo6fbm6vfj7Pfh7fjf7vjd7/na8fnY8vrW8/rU9PvS9fvP9/vN+PzL+fzJ+v3G+/3E/f7C/v7A"
        "/v6+/v28/vu6/vq4/vm2/vi0/vez/vWx/vSv/vOt/vKr/vGp/u+n/u6m/u2k/uyi/uqg/ume/uic/ueb/uaZ/uSX/uOV/uKT/uGR/uCQ/d6O/dyM/dqK/diI/daG/dSE"
        "/dKD/dCB/c5//cx9/cp7/ch5/cZ4/cR2/cJ0/cBy/b5w/bxu/bps/bhr/bZp/bRn/bJl/bBj/a5h/Kxg/Kpf/Kde+6Vc+6Jb+59a+p1Z+ppY+phX+ZVV+ZNU+JBT+I5S"
        "+ItR94lP94ZO94NN9oFM9n5L9nxK9XlI9XdH9XRG9HJF9G9E9G1D8mpB8WhA8GU/72M+7mE97V487Fw76lk66Vc56FU451I25lA15U005Esz4kky4UYx4EQw30Ev3j8u"
        "3T0t3Dor2jgq2TUp2DMo1zEn1i8m1C0m0ism0CkmzicmzCUmyiMmyCEmxiAmxB4mwhwmwBomvhgmvBYmuhQmuBImthAmtA8msg0msAsmrgkmrAcmqgUmqAMmpgEmpQAm"
    ),
    "terrain_land": (
        "AcxmAcxmBc1nCM1nDc5oDc5oEc9pFdBqGdFrGdFrHdFrIdJsJdNtJdNtKNRuLdVvMdVvMdVvNdZwOddxPdhyPdhyQdlzRdlzSNp0SNp0Tdt1Udx2Vd13Vd13Wd13Xd54"
        "Yd95Yd95ZeB6aOF6beF7beF7ceJ8deN9eeR+eeR+feV/geV/heaAheaAiOeBjeiCkemDkemDlemDmeqEneuFneuFoeyGpe2HqO2HqO2Hre6Ise+JtfCKtfCKufGLvfGL"
        "wfKMwfKMxfONyPSOzfWPzfWP0fWP1faQ2feR2feR3fiS4fmT5fmT5fmT6PqU7fuV8fyW8fyW9f2X+f2X/f6Y/v2Y/v2Y/PuX+viW+PaV+PaV9vOU9PCT8u6R8u6R8OuQ"
        "7umP7OaO7OaO6uSN6OGM5t+L5t+L5NyK4tmJ4NeI4NeI3tSH3NKG2s+F2s+F2M2D1sqC08eB08eB0sWA0MJ/zsB+zsB+zL19yrt8yLh7yLh7xrZ6w7N5wrB4wrB4wK52"
        "vqt1vKl0vKl0uqZzuKRytqFxtqFxs59wspxvsJlusJlurpdtrJRsqpJrqpJrqI9qpo1oo4pno4pnoodmoIVlnoJknoJknIBjmn1imHthmHthlnhgk3ZfknNeknNekHBd"
        "jm5bjGtajGtaimlZiGZYhmRXhmRXg2FWgl9VgFxUgV1WgV1Wg2BYhWJbh2Veh2VeiGdgi2pjjW1mjW1mj29okXJrk3Ruk3RulXdwl3lzmXx2mXx2m395nYF7n4R+n4R+"
        "oYaBo4mDpYuGpYuGp46JqJCLq5OOq5OOrZaRr5iTsZuWsZuWs52ZtaCbt6Ket6KeuaWhu6ejvaqmvaqmv62pwa+rw7Kuw7KuxLSxx7ezyLm2yLm2y7y5zb+7z8G+z8G+"
        "0cTB08bE1MnG1MnG18vJ2c7M29DO29DO3dPR39bU4djW4djW49vZ5N3c5+De5+De6OLh6+Xk7efm7efm7+rp8e3s8+/u8+/u9PLx9/T0+ff2+ff2+/n5/fz8////////"
    ),
    "ndvi_lts": (
        "bk4zb04zcE80cVA0clE1c1I1dFM2dVQ2dlU3eFY3eVc4elg4e1k5fFo5fVs6flw7f107gF48gl88g2A9hGE9hWI+hmM+h2Q/iGQ/iWVAimZAjGdBjWhBjmlCj2pDkGtD"
        "kWxEkm1Ek25FlG9FlnBGl3FGmHJHmXNHmnRIm3VInHZJnXdJn3hKoHlKoXpLonpMo3tMpHxNpX1Npn5Op39OqYBPqoFPq4JQrINQrYRRroVRr4ZSsIdSsYhTs4lUtIpU"
        "tYtVtYtVtYxVto1Wto1Wt45Wt49XuI9XuJBYuJFYuZFYuZJZupNZupNZu5Rau5Vau5VbvJZbvJdbvZdcvZhcvpldvpldvppdv5pev5tewJxewJxfwJ1fwZ5gwZ5gwp9g"
        "wqBhw6Bhw6Fhw6JixKJixKNjxaRjxaRjxqVkxqZkxqZkx6dlx6hlyKhmyKlmyapmyapnyatnyqxnyqxoy61oy65py65pzK9pzLBqzbBqzbFqzrJrzrJrzrNsz7Rsz7Rs"
        "z7RszrRszbRszLRry7RryrRrybRqyLRqx7RqxrRpxbRpxLRpw7RowrNowbNowLNnv7NnvrNnvbNmvLNmu7NmurNlubNluLNlt7NktrNktLJks7JjsrJjsbJjsLJir7Ji"
        "rrJirbJhrLJhq7JhqrJgqbJgqLFgp7FfprFfpbFfpLFfo7FeorFeobFeoLFdn7FdnrFdnbFcnLFcm7BcmrBbmbBbmLBbl7BalrBalbBalLBZk7BZkrBZkbBYkLBYj7BY"
        "ja9XjK5Wiq1Wia1Vh6xVhqtUhKpTg6lTgalSgKhSfqdRfaZQe6VQeqVPeKRPd6NOdaJOc6FNcqFMcKBMb59LbZ5LbJ1Kap1JaZxJZ5tIZppIZJlHY5lGYZhGYJdFXpZF"
        "XZVEW5RDWpRDWJNCV5JCVZFBVJFAUpBAUI8/T44/TY0+TI09Sow9SYs8R4o8Rok7RIk6Q4g6QYc5QIY5PoU4PYQ3O4Q3OoM2OII2N4E1NYE0NIA0Mn8zMX4zL30yLn0y"
    ),
}

NAMES = tuple(_TABLES)


@functools.lru_cache(maxsize=None)
def colormap_lut(name: str) -> np.ndarray:
    """Return the read-only 256×3 uint8 RGB table for a colour ramp named in :data:`NAMES`."""
    lut = np.frombuffer(base64.b64decode(_TABLES[name]), dtype=np.uint8).reshape(256, 3)
    lut.flags.writeable = False
    return lut
