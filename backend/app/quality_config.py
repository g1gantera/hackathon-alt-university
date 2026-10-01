"""Validated runtime configuration for the demonstration quality index."""
import copy
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_QUALITY_WEIGHTS={'schedule':42.0,'energy':18.0,'capacity':20.0,'conflicts':10.0,'arrival_accuracy':10.0}
DEFAULT_SETTINGS={'passenger_weight':3.0,'freight_weight':1.0,'delay_weight':.7,'energy_weight':.3,
                  'delay_norm_s':7200.0,'energy_norm_kwh':100000.0,'arrival_tolerance_s':300.0,
                  'quality_weights':None,'quality_formula':'weighted_mean',
                  'quality_threshold_normal':90.0,'quality_threshold_attention':70.0,'conflict_penalty':1.0}
Weight=Annotated[float,Field(ge=0,le=1000,strict=True)]


class QualityWeights(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,validate_default=True)
    schedule: Weight=42
    energy: Weight=18
    capacity: Weight=20
    conflicts: Weight=10
    arrival_accuracy: Weight=10

    @model_validator(mode='after')
    def nonzero(self):
        if sum(self.model_dump().values())<=0:
            raise ValueError('At least one quality weight must be positive')
        return self


class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,validate_default=True)
    passenger_weight: float=Field(3,gt=0,le=20,strict=True)
    freight_weight: float=Field(1,gt=0,le=20,strict=True)
    # Older clients may still submit the relative split of the original 60% share.
    delay_weight: float=Field(.7,ge=0,le=1,strict=True)
    energy_weight: float=Field(.3,ge=0,le=1,strict=True)
    delay_norm_s: float=Field(7200,gt=0,le=1000000,strict=True)
    energy_norm_kwh: float=Field(100000,gt=0,le=10000000,strict=True)
    arrival_tolerance_s: float=Field(300,ge=0,le=3600,strict=True)
    quality_weights: QualityWeights|None=None
    quality_formula: Literal['weighted_mean','weighted_geometric']='weighted_mean'
    quality_threshold_normal: float=Field(90,gt=0,le=100,strict=True)
    quality_threshold_attention: float=Field(70,ge=0,lt=100,strict=True)
    conflict_penalty: float=Field(1,gt=0,le=1000,strict=True)

    @model_validator(mode='after')
    def valid_quality(self):
        if self.quality_weights is None and self.delay_weight+self.energy_weight<=0:
            raise ValueError('At least one legacy metric weight must be positive')
        if self.quality_threshold_attention>=self.quality_threshold_normal:
            raise ValueError('Attention threshold must be lower than the normal threshold')
        return self


def quality_weights(settings):
    raw=settings.get('quality_weights')
    if raw is None:
        delay=settings.get('delay_weight',.7)
        energy=settings.get('energy_weight',.3)
        raw={**DEFAULT_QUALITY_WEIGHTS,'schedule':60*delay/(delay+energy),'energy':60*energy/(delay+energy)}
    total=sum(raw.values())
    return {key:value/total for key,value in raw.items()}


def canonical_settings(settings):
    result=copy.deepcopy({**DEFAULT_SETTINGS,**settings})
    if result['quality_weights'] is None:
        result['quality_weights']={key:round(value*100,10) for key,value in quality_weights(result).items()}
    return result


def combine_scores(scores,weights,method):
    """Return a 0..100 score and an additive explanation of its lost points."""
    if method=='weighted_mean':
        losses={key:(1-value)*weights[key]*100 for key,value in scores.items()}
        return max(0,min(100,100-sum(losses.values()))),losses
    # Zero-weight components have no effect, including zero scores (no log(0)).
    zero={key:weights[key] for key,value in scores.items() if value==0 and weights[key]>0}
    if zero:
        total=sum(zero.values())
        return 0,{key:100*zero.get(key,0)/total for key in scores}
    penalties={key:-weights[key]*math.log(value) if weights[key]>0 else 0 for key,value in scores.items()}
    total=sum(penalties.values())
    quality=100*math.exp(-total)
    return quality,{key:(100-quality)*penalty/total if total>0 else 0 for key,penalty in penalties.items()}


def quality_assessment(index,settings):
    if index>=settings['quality_threshold_normal']:
        return 'on_track'
    if index>=settings['quality_threshold_attention']:
        return 'attention'
    return 'disrupted'
