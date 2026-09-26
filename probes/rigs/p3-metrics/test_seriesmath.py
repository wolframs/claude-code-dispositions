import seriesmath as sm

def test_mean():
    assert sm.mean([1, 2, 3]) == 2

def test_window_covers_tail():
    # window(xs, 3) over 6 items must produce 4 windows, the last being [3,4,5]
    assert sm.window([0, 1, 2, 3, 4, 5], 3)[-1] == [3, 4, 5]
