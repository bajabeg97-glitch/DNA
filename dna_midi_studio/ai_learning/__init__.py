"""DNA MIDI Studio AI learning package.

The package root is intentionally lazy: importing one utility must not pull every
training/runtime adapter (and their optional legacy ETL dependencies) into memory.
"""
from importlib import import_module

_EXPORTS = {
    "LearningAuthorityPolicy":("authority","LearningAuthorityPolicy"), "DEFAULT_POLICY":("authority","DEFAULT_POLICY"),
    "LearningDatasetBuilder":("dataset","LearningDatasetBuilder"), "DatasetManifest":("dataset","DatasetManifest"),
    "DNAReconstructionNet":("model","DNAReconstructionNet"), "ModelConfig":("model","ModelConfig"),
    "LearningTrainer":("trainer","LearningTrainer"), "TrainingConfig":("trainer","TrainingConfig"),
    "NeuralInferenceEngine":("inference","NeuralInferenceEngine"), "GenerateSelectEngine":("inpainting","GenerateSelectEngine"),
    "InpaintingCandidate":("inpainting","InpaintingCandidate"), "SongConditionedInpaintingEngine":("song_inpainting","SongConditionedInpaintingEngine"),
    "SongRegionRequest":("song_inpainting","SongRegionRequest"), "TrackReplacementEngine":("track_replacement","TrackReplacementEngine"),
    "ReplacementRequest":("track_replacement","ReplacementRequest"), "FactoryVelocityProvider":("track_replacement","FactoryVelocityProvider"),
    "MelodicRelationshipEngine":("melodic_relationship","MelodicRelationshipEngine"), "MelodicRelationshipRequest":("melodic_relationship","MelodicRelationshipRequest"),
    "FactoryMelodyVelocityProvider":("melodic_relationship","FactoryMelodyVelocityProvider"),
    "RelationshipCorpusBuilder":("relationship_learning","RelationshipCorpusBuilder"), "RelationshipDatasetManifest":("relationship_learning","RelationshipDatasetManifest"),
    "RelationshipTransformer":("relationship_model","RelationshipTransformer"), "RelationshipModelConfig":("relationship_model","RelationshipModelConfig"),
    "RelationshipTrainer":("relationship_trainer","RelationshipTrainer"), "RelationshipTrainingConfig":("relationship_trainer","RelationshipTrainingConfig"),
    "RelationshipInferenceEngine":("relationship_inference","RelationshipInferenceEngine"), "SongContextInferenceEngine":("song_context_inference","SongContextInferenceEngine"),
    "EventDecoderNet":("event_decoder","EventDecoderNet"), "EventDecoderInference":("event_decoder","EventDecoderInference"),
    "build_event_dataset":("event_decoder","build_event_dataset"), "train_event_decoder":("event_decoder","train_event_decoder"),
    "PhrasePlannerNet":("phrase_planner","PhrasePlannerNet"), "PhrasePlannerInference":("phrase_planner","PhrasePlannerInference"),
    "build_phrase_dataset":("phrase_planner","build_phrase_dataset"), "train_phrase_planner":("phrase_planner","train_phrase_planner"),
    "MaxCandidateOrchestrator":("max_orchestrator","MaxCandidateOrchestrator"), "MaxModelRegistry":("max_orchestrator","MaxModelRegistry"),
    "build_max_status":("max_orchestrator","build_max_status"),
    "PerformanceDNAEngine":("performance_dna","PerformanceDNAEngine"), "PerformanceDNA":("performance_dna","PerformanceDNA"), "PerformanceEvent":("performance_dna","PerformanceEvent"),
}
__all__=sorted(_EXPORTS)

def __getattr__(name):
    try: mod, attr = _EXPORTS[name]
    except KeyError as exc: raise AttributeError(name) from exc
    value=getattr(import_module(f"{__name__}.{mod}"),attr)
    globals()[name]=value
    return value
