import re
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Cue(StrictModel):
    start: float = Field(ge=0, le=180)
    end: float = Field(gt=0, le=180)
    text: str = Field(min_length=1, max_length=160)

    @model_validator(mode='after')
    def check(self):
        if self.end <= self.start:
            raise ValueError('Caption end must follow its start')
        # Caption text may be Roman Telugu or Telugu script; media.write_ass picks a Telugu font.
        if not self.text.strip():
            raise ValueError('Caption text cannot be empty')
        return self


class Scene(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,48}$')
    narration: str = Field(default='', max_length=3000)
    spoken_text: str = Field(default='', max_length=3000)
    narration_source_roman: str = Field(default='', max_length=3000)
    roman: str = Field(default='', max_length=3000)
    keywords: str = Field(default='', max_length=100)
    generation_prompt: str = Field(default='', max_length=6000)
    prompt_seconds: int = Field(default=5, ge=3, le=15)
    stock_provider: Literal['auto', 'pixabay', 'pexels'] = 'auto'
    short_clip_mode: Literal['loop', 'freeze'] = 'loop'
    visual: Literal['upload', 'stock', 'image', 'graphic'] = 'upload'
    video_asset: str = Field(default='', pattern=r'^[a-f0-9]{0,32}$')
    audio_asset: str = Field(default='', pattern=r'^[a-f0-9]{0,32}$')
    audio_text_hash: str = ''
    trim_start: float = Field(default=0, ge=0, le=36000)
    trim_end: float = Field(default=0, ge=0, le=36000)  # 0 = the clip's end
    fit: Literal['crop', 'contain'] = 'crop'
    frame_aspect: Literal['full', '4:5', '1:1', '16:9'] = 'full'
    zoom: float = Field(default=1.0, ge=1, le=3)
    crop_x: float = Field(default=0.5, ge=0, le=1)
    crop_y: float = Field(default=0.5, ge=0, le=1)
    hold_seconds: float = Field(default=0, ge=0, le=30)
    graphic_title: str = Field(default='', max_length=150)
    rights_reviewed: bool = False
    narration_block_id: str = Field(default='', max_length=48)
    take_id: str = Field(default='', max_length=64)
    delivery_preset: str = Field(default='conversational', max_length=32)
    cues: list[Cue] = Field(default_factory=list, max_length=200)

    @model_validator(mode='after')
    def check(self):
        if re.search(r'[\u0c00-\u0c7f]', self.roman):
            raise ValueError('Roman caption field must use English letters, not Telugu script')
        if self.trim_end and self.trim_end <= self.trim_start:
            raise ValueError('Trim end must be after trim start')
        last = 0.0
        for cue in self.cues:
            if cue.start < last:
                raise ValueError('Caption cues must be ordered and must not overlap')
            last = cue.end
        return self


class Project(StrictModel):
    title: str = Field(default='Untitled reel', min_length=1, max_length=160)
    topic: str = Field(default='', max_length=3000)
    category: str = Field(default='Storytelling', max_length=60)
    target_seconds: int = Field(default=60, ge=10, le=180)
    duration_mode: Literal['natural', 'target'] = 'natural'
    tts_provider: str = Field(default='edge', max_length=32)
    voice: str = Field(default='te-IN-MohanNeural', max_length=64)
    speech_rate: int = Field(default=0, ge=-25, le=25)
    delivery_preset: str = Field(default='conversational', max_length=32)
    pronunciation_dict: dict[str, str] = Field(default_factory=dict)
    voice_locked: bool = False
    voice_profile: dict = Field(default_factory=dict)
    narration_blocks: list[dict] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list, max_length=30)
    evidence_notes: str = Field(default='', max_length=30000)
    research_report: str = Field(default='', max_length=60000)
    research_sources: list[dict] = Field(default_factory=list, max_length=100)
    research_grounding: dict = Field(default_factory=dict)
    music_asset: str = Field(default='', pattern=r'^[a-f0-9]{0,32}$')
    music_gain: float = Field(default=0.12, ge=0, le=0.5)
    music_rights_reviewed: bool = False
    caption_size: int = Field(default=56, ge=30, le=90)
    caption_bottom: int = Field(default=300, ge=180, le=600)
    # Projects saved before caption styles keep the original outlined captions.
    caption_style: Literal['classic', 'black_box', 'yellow_accent', 'translucent'] = 'classic'
    script_approved_hash: str = Field(default='', max_length=64)
    draft_script: str = Field(default='', max_length=50000)

    @model_validator(mode='after')
    def check(self):
        ids = [s.id for s in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError('Scene IDs must be unique')
        return self
