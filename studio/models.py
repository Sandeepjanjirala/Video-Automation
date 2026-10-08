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
        if re.search(r'[\u0c00-\u0c7f]', self.text):
            raise ValueError('Captions must use Roman Telugu (English letters)')
        return self


class Scene(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,48}$')
    narration: str = Field(default='', max_length=3000)
    roman: str = Field(default='', max_length=3000)
    keywords: str = Field(default='', max_length=100)
    visual: Literal['upload', 'stock', 'graphic'] = 'upload'
    video_asset: str = Field(default='', pattern=r'^[a-f0-9]{0,32}$')
    audio_asset: str = Field(default='', pattern=r'^[a-f0-9]{0,32}$')
    audio_text_hash: str = ''
    trim_start: float = Field(default=0, ge=0, le=36000)
    fit: Literal['crop', 'contain'] = 'crop'
    crop_x: float = Field(default=0.5, ge=0, le=1)
    hold_seconds: float = Field(default=0, ge=0, le=30)
    graphic_title: str = Field(default='', max_length=150)
    rights_reviewed: bool = False
    cues: list[Cue] = Field(default_factory=list, max_length=200)

    @model_validator(mode='after')
    def check(self):
        if re.search(r'[\u0c00-\u0c7f]', self.roman):
            raise ValueError('Roman caption field must use English letters, not Telugu script')
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
    voice: Literal['te-IN-ShrutiNeural', 'te-IN-MohanNeural'] = 'te-IN-ShrutiNeural'
    speech_rate: int = Field(default=0, ge=-25, le=25)
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
    script_approved_hash: str = Field(default='', max_length=64)
    draft_script: str = Field(default='', max_length=50000)

    @model_validator(mode='after')
    def check(self):
        ids = [s.id for s in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError('Scene IDs must be unique')
        return self
