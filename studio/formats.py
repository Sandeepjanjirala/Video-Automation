"""Video formats: an Instagram Reel (vertical 9:16, the default) or a long-form YouTube video (16:9).

studio/static/js/state.js mirrors FORMATS for the editor (frame size, captions and limits).
"""

FORMATS = {
    'reel': {
        'label': 'Instagram Reel', 'final': (1080, 1920), 'draft': (540, 960),
        'max_scenes': 30, 'max_seconds': 180, 'shot_seconds': 4.0, 'caption_chars': 32,
        'orientation': 'vertical 9:16 Instagram Reel',
    },
    'youtube': {
        'label': 'YouTube video', 'final': (1920, 1080), 'draft': (960, 540),
        'max_scenes': 400, 'max_seconds': 3600, 'shot_seconds': 6.0, 'caption_chars': 48,
        'orientation': 'landscape 16:9 long-form YouTube video',
    },
}


def of(project):
    """The format settings of a project body (projects saved before formats are reels)."""
    return FORMATS.get((project or {}).get('format') or 'reel', FORMATS['reel'])


def size(project, quality='final'):
    """(width, height) of a draft or final render."""
    return of(project)['draft' if quality == 'draft' else 'final']


def landscape(project):
    width, height = size(project)
    return width > height


def caption_margin(project):
    """Bottom caption margin in output pixels. The project's margin is set for the 1920-pixel-tall
    reel; a 1080-pixel-tall landscape frame keeps captions proportionally lower."""
    bottom = int((project or {}).get('caption_bottom', 300))
    return round(bottom * 0.28) if landscape(project) else bottom
