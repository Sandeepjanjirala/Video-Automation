"""Scene framing shared by the renderer and the exact-frame preview.

Geometry is computed in the display space FFmpeg filters see (after automatic
rotation and square-pixel correction). studio/static/js/framing.js mirrors
`window` and `geometry` operation for operation, so the browser preview places
media at the same even pixel offsets the renderer crops and pads to.
"""
import functools
import json
import math
from pathlib import Path

from .config import executable

ASPECTS = {'full': None, '4:5': (4, 5), '1:1': (1, 1), '16:9': (16, 9)}
BACKGROUND = '0x12251F'
FRAME = 1 / 30
# Every scene leaves the framing scale as BT.709 limited range, so the joined reel never
# switches colour range or matrix between scenes (JPEGs are full-range BT.601).
MATRICES = {'bt709': 'bt709', 'bt470bg': 'bt470', 'smpte170m': 'smpte170m', 'fcc': 'fcc',
            'smpte240m': 'smpte240m', 'bt2020nc': 'bt2020', 'bt2020c': 'bt2020'}
COLOUR_TAGS = 'setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709'


def even(value):
    """Nearest even integer, rounding halves up (matches JS Math.floor)."""
    return int(math.floor(value / 2 + 0.5)) * 2


def window(width, height, aspect):
    """Visible media window (x, y, w, h): the largest box of that shape, centred in the frame
    (full width in a vertical reel, full height in a landscape video)."""
    ratio = ASPECTS.get(aspect)
    if not ratio:
        return 0, 0, width, height
    if width * ratio[1] > height * ratio[0]:
        w = min(width, even(height * ratio[0] / ratio[1]))
        return even((width - w) / 2), 0, w, height
    h = min(height, even(width * ratio[1] / ratio[0]))
    return 0, even((height - h) / 2), width, h


def geometry(src_w, src_h, width, height, fit='crop', aspect='full', zoom=1.0, x=0.5, y=0.5):
    """Scale the source, crop the part inside the window, and place it on the frame.

    Coordinates are in output pixels: the source is scaled to scale_w x scale_h,
    the crop rectangle (crop_x, crop_y, crop_w, crop_h) is cut from it, and the
    result is placed at (pad_x, pad_y). Every value is even, so 4:2:0 video is
    never rounded differently by FFmpeg.
    """
    wx, wy, ww, wh = window(width, height, aspect)
    if fit == 'crop':
        base = max(ww / src_w, wh / src_h)
    else:
        base = min(ww / src_w, wh / src_h)
    scale = base * zoom
    sw = max(2, even(src_w * scale))
    sh = max(2, even(src_h * scale))

    def axis(scaled, room, pos, origin):
        if scaled > room:
            return room, even((scaled - room) * pos), origin
        return scaled, 0, origin + even((room - scaled) / 2)

    cw, cx, px = axis(sw, ww, x, wx)
    ch, cy, py = axis(sh, wh, y, wy)
    return {'width': width, 'height': height, 'window': [wx, wy, ww, wh],
            'scale_w': sw, 'scale_h': sh, 'crop_w': cw, 'crop_h': ch,
            'crop_x': cx, 'crop_y': cy, 'pad_x': px, 'pad_y': py}


def scene_geometry(scene, source, width, height):
    return geometry(source['width'], source['height'], width, height,
                    scene.get('fit', 'crop'), scene.get('frame_aspect', 'full'),
                    scene.get('zoom', 1.0), scene.get('crop_x', 0.5), scene.get('crop_y', 0.5))


def filter_chain(g, source):
    """FFmpeg filters producing `g` from the display-space source.

    Normally the whole source is scaled to scale_w x scale_h and cropped, which
    is exactly what the preview shows. When zooming would make that intermediate
    picture larger than four output frames, only the source rectangle covering
    the visible area is scaled instead (within half an output pixel of `g`).
    Output is converted to BT.709 limited range (see MATRICES); untagged sources
    are treated as BT.709, as players treat untagged HD video.
    """
    src_w, src_h = source['width'], source['height']
    colour = f"in_color_matrix={source.get('matrix', 'bt709')}:out_color_matrix=bt709:out_range=tv"
    steps = [f"scale={src_w}:{src_h},setsar=1"] if source.get('normalize') else []
    place = f"pad={g['width']}:{g['height']}:{g['pad_x']}:{g['pad_y']}:color={BACKGROUND},setsar=1"
    if g['scale_w'] * g['scale_h'] <= 4 * g['width'] * g['height']:
        steps += [f"scale={g['scale_w']}:{g['scale_h']}:flags=bicubic:{colour}",
                  f"crop=w={g['crop_w']}:h={g['crop_h']}:x={g['crop_x']}:y={g['crop_y']}:exact=1", place]
        return ','.join(steps)
    sx, sy = g['scale_w'] / src_w, g['scale_h'] / src_h

    def axis(crop, size, scale, limit):
        # Spare source pixels give bicubic scaling real neighbours at the edges; even
        # offsets keep 4:2:0 chroma aligned with luma.
        lo = max(0, 2 * math.floor(crop / scale / 2) - 2)
        hi = min(limit, 2 * math.ceil((crop + size) / scale / 2) + 2)
        scaled = max(size, int(math.floor((hi - lo) * scale + 0.5)))
        offset = min(max(0, int(math.floor(crop - lo * scale + 0.5))), scaled - size)
        return lo, hi - lo, scaled, offset

    x0, rw, scaled_w, ox = axis(g['crop_x'], g['crop_w'], sx, src_w)
    y0, rh, scaled_h, oy = axis(g['crop_y'], g['crop_h'], sy, src_h)
    steps += [f"crop=w={rw}:h={rh}:x={x0}:y={y0}:exact=1",
              f"scale={scaled_w}:{scaled_h}:flags=bicubic:{colour}",
              f"crop=w={g['crop_w']}:h={g['crop_h']}:x={ox}:y={oy}:exact=1", place]
    return ','.join(steps)


def _ratio(text):
    try:
        num, den = (int(v) for v in str(text).split(':'))
        return num / den if num > 0 and den > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def _rotation(items):
    for item in items or []:
        if 'rotation' in item:
            return int(round(float(item['rotation']))) % 360
    return 0


@functools.lru_cache(maxsize=256)
def _source_info(path, mtime_ns, size):
    from .media import run
    args = [executable('ffprobe'), '-v', 'error', '-select_streams', 'v:0', '-show_streams', '-show_format']
    stream_info = json.loads(run(args + ['-of', 'json', path], timeout=45))
    stream = (stream_info.get('streams') or [{}])[0]
    # The first decoded frame carries EXIF orientation for photos; streams carry it for video.
    frames = json.loads(run(args[:-2] + ['-read_intervals', '%+#1', '-show_frames', '-of', 'json', path],
                            timeout=45)).get('frames') or []
    frame = frames[0] if frames else {}
    w = int(frame.get('width') or stream.get('width') or 0)
    h = int(frame.get('height') or stream.get('height') or 0)
    if w <= 0 or h <= 0:
        raise ValueError('Could not read the picture size of this media')
    sar = _ratio(frame.get('sample_aspect_ratio') or stream.get('sample_aspect_ratio'))
    rotation = _rotation(frame.get('side_data_list')) or _rotation(stream.get('side_data_list'))
    normalize = abs(sar - 1) > 1e-6
    if normalize:
        w = even(w * sar)
    if rotation in (90, 270):
        w, h = h, w
    try:
        duration = float(stream_info.get('format', {}).get('duration') or 0)
    except ValueError:
        duration = 0.0
    return {'width': w, 'height': h, 'rotation': rotation, 'normalize': normalize,
            'matrix': MATRICES.get(frame.get('color_space') or stream.get('color_space'), 'bt709'),
            'duration': duration if math.isfinite(duration) else 0.0}


def source_info(path):
    """Display size (after rotation and pixel aspect), rotation and duration."""
    path = Path(path)
    st = path.stat()
    return dict(_source_info(str(path), st.st_mtime_ns, st.st_size))


def segment(scene, source_duration):
    """The trimmed part of a video clip as (start, end) seconds."""
    start = float(scene.get('trim_start') or 0)
    end = float(scene.get('trim_end') or 0) or source_duration
    end = min(end, source_duration)
    if start >= source_duration - 0.05:
        raise ValueError('Trim start must be before the end of the video')
    if end - start < 0.1:
        raise ValueError('The trimmed clip must be at least 0.1 seconds long')
    return start, end


SHOT_SECONDS = 4.0  # a reel scene cuts to its next extra shot at least this often (formats.py)


def scene_shots(scene, length, shot_seconds=SHOT_SECONDS):
    """The clips a video scene shows in turn: its own, then its extra shots, one per ~4 seconds
    in a reel (`shot_seconds`; ~6 in a YouTube video).

    Extra shots play from their start, cropped to fill the frame; the scene's own framing and trim
    apply to its own clip only.
    """
    extras = scene.get('extra_shots') or []
    if scene.get('visual') not in ('upload', 'stock') or not extras:
        return [scene]
    count = min(1 + len(extras), max(1, math.ceil(length / shot_seconds - 0.05)))
    return [scene] + [{**scene, 'video_asset': aid, 'visual': 'stock', 'trim_start': 0, 'trim_end': 0, 'fit': 'crop',
                       'frame_aspect': 'full', 'zoom': 1.0, 'crop_x': 0.5, 'crop_y': 0.5, 'short_clip_mode': 'loop'}
                      for aid in extras[:count - 1]]


def visual_inputs(scene, path, source, length, width, height, work, run, job_id=None):
    """FFmpeg input arguments and video filter for one scene's uploaded visual.

    A short segment that loops is cut and framed once first: -stream_loop
    restarts at the file's beginning, not at the trim start.
    """
    chain = filter_chain(scene_geometry(scene, source, width, height), source)
    if scene['visual'] == 'image':
        return ['-loop', '1', '-framerate', '30', '-i', path], chain
    start, end = segment(scene, source['duration'])
    span = end - start
    whole = start == 0 and end >= source['duration']
    freeze = scene.get('short_clip_mode') == 'freeze'
    if span >= length:
        return ['-ss', f'{start:.3f}', '-i', path], chain
    if freeze:
        return (['-ss', f'{start:.3f}', '-t', f'{span:.3f}', '-i', path],
                chain + f',tpad=stop_mode=clone:stop_duration={length}')
    if whole:
        return ['-stream_loop', '-1', '-i', path], chain
    cut = Path(work) / 'segment.mp4'
    run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{start:.3f}', '-i', path,
         '-t', f'{span:.3f}', '-map', '0:v:0', '-an', '-vf', chain + ',fps=30', '-c:v', 'libx264',
         '-preset', 'veryfast', '-crf', '12', '-pix_fmt', 'yuv420p', '-threads', '4', cut], job_id=job_id)
    return ['-stream_loop', '-1', '-i', cut], 'null'


def preview_frame(scene, path, source, width, height, source_time, work, project, caption=''):
    """Render one frame with the renderer's filters (and an optional caption) to JPEG bytes."""
    from .media import CAPTION_FILTER, run, write_ass
    g = scene_geometry(scene, source, width, height)
    vf = filter_chain(g, source)
    args = [executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y']
    if scene['visual'] != 'image':
        start, end = segment(scene, source['duration'])
        at = min(max(float(source_time if source_time is not None else start), start), max(start, end - FRAME))
        # Scene frame k shows the first source frame at or after start + k/30, as does
        # seeking there; snapping makes a mid-frame playhead show the frame on screen.
        at = start + math.floor((at - start) * 30 + 1e-6) / 30
        args += ['-ss', f'{max(0.0, at - 0.0001):.6f}']
    args += ['-i', path]
    if caption.strip():
        write_ass(Path(work) / 'captions.ass', [{'start': 0, 'end': 60, 'text': caption.strip()}], project)
        vf += ',' + CAPTION_FILTER
    # JPEG viewers assume full-range BT.601; convert from the export's BT.709 limited range.
    vf += ',scale=in_color_matrix=bt709:in_range=tv:out_color_matrix=bt601:out_range=pc'
    output = Path(work) / 'frame.jpg'
    run(args + ['-frames:v', '1', '-vf', vf, '-q:v', '2', output], cwd=work, timeout=90)
    return output.read_bytes(), g


def title_frame(scene, width, height, work, project, caption=''):
    """A title-card scene's frame, as the renderer draws it, one second in (after the title fades in)."""
    from .media import CAPTION_FILTER, run, write_ass
    cues = [{'start': 0, 'end': 60, 'text': caption.strip()}] if caption.strip() else []
    write_ass(Path(work) / 'captions.ass', cues, project, scene.get('graphic_title') or None, 60)
    vf = (f"drawbox=x=0:y={int(height * .24)}:w=iw:h=4:color=0xB9F8CF:t=fill,{CAPTION_FILTER},"
          'scale=in_color_matrix=bt709:in_range=tv:out_color_matrix=bt601:out_range=pc')
    output = Path(work) / 'frame.jpg'
    run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
         '-i', f'color=c={BACKGROUND}:s={width}x{height}:r=30:d=2', '-vf', vf, '-ss', '1', '-frames:v', '1',
         '-q:v', '2', output], cwd=work, timeout=90)
    return output.read_bytes()
