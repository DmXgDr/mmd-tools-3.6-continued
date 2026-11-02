"""Conservative, data-only detection of persistent VMD motion discontinuities.

The returned integers are *source VMD* frame numbers immediately after a jump.
They are candidates for moving that frame from ``2*f`` to ``2*f-1`` on import;
this module never edits animation data or evaluates a Blender scene.

The detector deliberately prefers missing an ambiguous cut to changing normal
motion.  It needs three source frames of context on both sides, rejects short
out-and-back pulses, and treats translation and quaternion orientation as two
geometric quantities, not seven independent F-curve votes.  It cannot infer
the author's intent or see jumps generated only by constraints/IK/physics.

Typical sorted tracks take O(n) time and O(n) auxiliary storage.  Unsorted
tracks are sorted once.  Repeated/invalid frame numbers reject the track;
non-finite channel values disqualify the affected local neighborhood.
"""
import math


_CONTEXT_FRAMES = 3
_RETURN_FRAMES = 6
_JUMP_RATIO = 8.0
_BONE_SPAN_RATIO = 0.4
_MORPH_SPAN_RATIO = 0.2
_RETURN_DISTANCE_RATIO = 0.25


def _ordered(frames):
    """Keep caller data untouched, including its ordering and quaternion signs."""
    records = list(frames)
    numbered = []
    try:
        for frame in records:
            number = frame.frame_number
            integer = int(number)
            if integer != number or integer < 0 or integer > 0xffffffff:
                return [], []
            numbered.append((integer, frame))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return [], []
    if any(a[0] > b[0] for a, b in zip(numbered, numbered[1:])):
        numbered.sort(key=lambda pair: pair[0])
    if any(a[0] == b[0] for a, b in zip(numbered, numbered[1:])):
        return [], []
    return [pair[0] for pair in numbered], [pair[1] for pair in numbered]


def _finite_tuple(values, length):
    try:
        result = tuple(float(value) for value in values)
        if len(result) != length or not all(math.isfinite(v) for v in result):
            return None
        return result
    except (TypeError, ValueError, OverflowError):
        return None


def _unit_quaternion(values):
    # VMD stores xyzw, while Blender's constructor commonly uses wxyz.  The
    # dot-product metric works directly on xyzw without reordering it.
    q = _finite_tuple(values, 4)
    if q is None:
        return None
    x, y, z, w = q
    squared = x*x + y*y + z*z + w*w
    if 0.0 < squared < math.inf:
        inverse = 1.0 / math.sqrt(squared)
        return (x*inverse, y*inverse, z*inverse, w*inverse)
    # Overflow/underflow is unusual in VMD, but handle finite scaled inputs
    # correctly instead of turning them into a false orientation jump.
    scale = max(abs(x), abs(y), abs(z), abs(w))
    if scale == 0.0:
        return None
    x, y, z, w = x/scale, y/scale, z/scale, w/scale
    inverse = 1.0 / math.sqrt(x*x + y*y + z*z + w*w)
    return (x*inverse, y*inverse, z*inverse, w*inverse)


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _angle(a, b):
    # q and -q denote the same orientation.  abs(dot) eliminates false cuts
    # from equivalent quaternion signs and gives the shortest physical angle.
    dot = abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3])
    return 2.0 * math.acos(min(1.0, dot))


def _scalar_distance(a, b):
    return abs(a - b)


def _deltas(values, distance):
    result = []
    for a, b in zip(values, values[1:]):
        delta = None if a is None or b is None else distance(a, b)
        result.append(delta if delta is not None and math.isfinite(delta) else None)
    return result


def _quiet_side(numbers, values, deltas, distance, anchor, direction,
                jump, span_ratio, linear_scalar):
    """Require three frames of stable context, even across a long hold key.

    Morph weights interpolate linearly in VMD, so an enclosing segment can be
    evaluated exactly at the context boundary.  Bone curves can be nonlinear:
    for a segment crossing that boundary, use its whole change conservatively
    instead of assuming a low average velocity implies a locally slow curve.
    """
    remaining = _CONTEXT_FRAMES
    cursor = anchor
    origin = values[anchor]
    max_speed = jump / _JUMP_RATIO
    max_span = jump * span_ratio
    while remaining > 0:
        other = cursor + direction
        if other < 0 or other >= len(numbers) or values[other] is None:
            return False
        edge = min(cursor, other)
        change = deltas[edge]
        if change is None:
            return False
        gap = abs(numbers[other] - numbers[cursor])
        covered = min(gap, remaining)
        divisor = gap if linear_scalar else covered
        if change / divisor > max_speed:
            return False
        boundary = values[other]
        if linear_scalar and gap > covered:
            fraction = covered / gap
            boundary = values[cursor] + (values[other] - values[cursor]) * fraction
        if distance(origin, boundary) > max_span:
            return False
        remaining -= covered
        cursor = other
    return True


def _short_return(numbers, values, distance, i, jump):
    """Reject a short A->B->A pulse, including either edge of the pulse."""
    tolerance = jump * _RETURN_DISTANCE_RATIO
    # Look ahead from B for a return to A, and backward from A for a recent B.
    for start, stop, step, anchor, target in (
            (i + 2, len(numbers), 1, i + 1, i),
            (i - 1, -1, -1, i, i + 1)):
        for k in range(start, stop, step):
            if abs(numbers[k] - numbers[anchor]) > _RETURN_FRAMES:
                break
            if values[k] is None:
                return True
            if distance(values[k], values[target]) <= tolerance:
                return True
    return False


def _is_step(numbers, values, deltas, distance, i, minimum, span_ratio,
             linear_scalar=False):
    jump = deltas[i]
    if jump is None or jump < minimum or jump <= 0.0:
        return False
    if not _quiet_side(numbers, values, deltas, distance, i, -1,
                       jump, span_ratio, linear_scalar):
        return False
    if not _quiet_side(numbers, values, deltas, distance, i + 1, 1,
                       jump, span_ratio, linear_scalar):
        return False
    return not _short_return(numbers, values, distance, i, jump)


def detect_bone_cuts(frames, *, minimum_translation=0.1,
                     minimum_rotation_degrees=12.0):
    """Return conservative per-bone cut frames from VMD BoneFrameKey objects.

    Translation is measured in original VMD units, before the import scale.
    Thus changing Blender's import scale cannot change detection.  Callers
    analyzing a format with different *source* units may scale the translation
    floor correspondingly.  Rotation is normalized and sign-independent.

    A jump in either position or orientation can qualify.  The caller should
    move all seven imported channels for that bone together, never individual
    quaternion components.  It must not apply a candidate to unrelated bones.
    """
    if minimum_translation < 0 or not math.isfinite(minimum_translation):
        raise ValueError('minimum_translation must be finite and non-negative')
    if not 0 <= minimum_rotation_degrees <= 180:
        raise ValueError('minimum_rotation_degrees must be between 0 and 180')
    numbers, records = _ordered(frames)
    if len(records) < 4:
        return set()
    # Fully baked files often repeat thousands of identical keys on unused
    # bones.  Such a track cannot contain a discontinuity; avoid quaternion
    # normalization and distance arrays for it.  This is exact equality, not
    # an approximation or a threshold that could suppress a small real cut.
    try:
        first_location, first_rotation = records[0].location, records[0].rotation
        if all(key.location == first_location and key.rotation == first_rotation
               for key in records[1:]):
            return set()
    except (AttributeError, TypeError, ValueError):
        pass
    locations, rotations = [], []
    for key in records:
        try:
            loc = _finite_tuple(key.location, 3)
            rot = _unit_quaternion(key.rotation)
        except AttributeError:
            loc = rot = None
        # A malformed sample is not a reliable full-pose endpoint, even if
        # only one of its location/rotation groups happens to be valid.
        if loc is None or rot is None:
            loc = rot = None
        locations.append(loc)
        rotations.append(rot)
    positions = _deltas(locations, _distance)
    angles = _deltas(rotations, _angle)
    angle_floor = math.radians(minimum_rotation_degrees)
    result = set()
    for i, (a, b) in enumerate(zip(numbers, numbers[1:])):
        if b - a != 1:
            continue
        if (_is_step(numbers, locations, positions, _distance, i,
                     minimum_translation, _BONE_SPAN_RATIO)
                or _is_step(numbers, rotations, angles, _angle, i,
                            angle_floor, _BONE_SPAN_RATIO)):
            result.add(b)
    return result


def detect_morph_cuts(frames, *, minimum_weight_change=0.25):
    """Return sustained weight steps; ordinary ramps and short blinks stay put.

    A sparse segment with equal endpoint weights is valid holding context;
    three source frames are required, not three separately recorded keys.
    Single-key endings and short out-and-back pulses remain untouched.
    """
    if minimum_weight_change < 0 or not math.isfinite(minimum_weight_change):
        raise ValueError('minimum_weight_change must be finite and non-negative')
    numbers, records = _ordered(frames)
    if len(records) < 4:
        return set()
    values = []
    for key in records:
        try:
            weight = float(key.weight)
            values.append(weight if math.isfinite(weight) else None)
        except (AttributeError, TypeError, ValueError, OverflowError):
            values.append(None)
    deltas = _deltas(values, _scalar_distance)
    return {b for i, (a, b) in enumerate(zip(numbers, numbers[1:]))
            if b - a == 1 and _is_step(numbers, values, deltas,
                                       _scalar_distance, i,
                                       minimum_weight_change,
                                       _MORPH_SPAN_RATIO, True)}
