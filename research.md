# ENERVERA: A Layered, Knowledge-Graph-First Pipeline for Automated Complete Blood Count Report Interpretation

## Abstract

Clinical laboratory interpretation remains a critical but error-prone task in modern healthcare workflows. Complete Blood Count (CBC) reports contain dense numerical and textual information that must be normalized, contextualized, and interpreted with respect to patient demographics, reference ranges, and clinical knowledge. This paper presents ENERVERA, a layered, knowledge-graph-first system designed to transform raw CBC report images or structured biomarker values into clinically meaningful findings, evidence-based recommendations, and patient-friendly reports. The proposed architecture combines optical character recognition (OCR), deterministic normalization, feature generation, graph-based reasoning over a clinical knowledge graph, confidence validation, and constrained large-language-model presentation. Unlike many black-box diagnostic systems, ENERVERA is designed to preserve traceability, clinical explainability, and modular extensibility across laboratory panels. The system is implemented as a FastAPI backend with a six-layer pipeline and is organized around domain-specific knowledge modules for CBC interpretation.

## Keywords

Clinical decision support; CBC interpretation; knowledge graph; laboratory informatics; OCR; explainable AI; healthcare automation

## 1. Introduction

The interpretation of laboratory reports is central to clinical decision-making. In hematology, Complete Blood Count (CBC) reports provide essential information for screening, monitoring, and diagnosing conditions such as anemia, infection, inflammation, and hematologic malignancies. However, extracting meaningful insights from these reports is challenging because the data are often presented in heterogeneous formats, may contain OCR errors, require unit conversion, and must be evaluated against age-, sex-, and context-specific reference ranges.

Traditional interpretation workflows depend heavily on human expertise and are susceptible to variability, fatigue, and delays. Meanwhile, recent advances in artificial intelligence have created new opportunities for automating parts of the workflow. However, many existing approaches rely on opaque machine learning models that lack transparency, are difficult to audit, and may produce conclusions without a clear clinical rationale. In healthcare settings, explainability and trust are not optional—they are operational requirements.

This paper introduces ENERVERA, a research-oriented system that addresses these challenges by combining deterministic clinical logic with graph-based reasoning and constrained language generation. The goal is not to replace clinicians, but to provide a transparent, auditable, and extensible decision support layer that assists interpretation and improves workflow efficiency.

## 2. Background and Motivation

Laboratory interpretation involves several stages:

1. Extracting values from a report image or document.
2. Normalizing them into canonical units and reference-based scales.
3. Deriving clinically meaningful features from the values.
4. Inferring likely conditions or findings using medical knowledge.
5. Validating the confidence and urgency of the interpretation.
6. Presenting the result in a form usable by patients and clinicians.

A robust system must handle noisy inputs, inconsistent labels, non-standard units, and incomplete patient context. It must also remain modular so that new panels, biomarkers, or clinical rules can be added without redesigning the whole pipeline.

The motivation for ENERVERA arises from the limitations of purely statistical prediction models and purely rule-based systems. Statistical models can identify patterns in data but often lack clinically grounded explanation. Rule-based systems are interpretable but can be brittle and difficult to extend. ENERVERA attempts to bridge this gap by combining symbolic reasoning, structured clinical knowledge, and controlled natural language generation.

## 3. Related Work

Several research directions have addressed automated medical report interpretation. Early approaches focused on optical character recognition (OCR) and rule-based extraction from structured forms. These systems performed well in controlled settings but were often brittle when confronted with document variability, poor image quality, or inconsistent labeling.

More recent systems have explored machine learning for medical image analysis, document understanding, and clinical text interpretation. These methods can improve recognition accuracy but often struggle with explainability and may require large labeled datasets. In clinical decision support, many systems also treat interpretation as a direct classification problem, mapping values to diseases without exposing intermediate evidence or reasoning paths.

Knowledge graphs have gained attention in biomedical informatics because they can represent relationships among biomarkers, thresholds, diseases, and recommendations. Such graphs provide a structured foundation for reasoning that is more explainable than end-to-end black-box methods. The introduction of large language models (LLMs) has further expanded the capacity for report summarization and patient communication, but LLMs alone are not sufficient for clinical reliability because they may invent facts, overgeneralize, or fail to preserve deterministic evidence.

ENERVERA differentiates itself by combining these capabilities into a layered architecture where each stage has explicit input-output contracts, traceable intermediate outputs, and a clear role in the overall reasoning pipeline.

## 4. System Overview

ENERVERA is designed as a six-layer pipeline that processes CBC report information from raw input to final presentation.

### 4.1 Layer 1: Extraction

The first layer converts PDF or image-based laboratory reports into structured biomarker rows. This stage uses OCR-based extraction to identify values, units, and labels. The objective is to recover a structured representation of the report even when the original document is noisy or poorly formatted.

### 4.2 Layer 2: Normalization

The second layer converts the extracted values into canonical representations. This includes mapping raw biomarker names to standard identifiers, converting units into a consistent format, applying reference ranges based on demographic context, and flagging quality issues or impossible values. Normalization is essential because clinical interpretation depends on consistent measurement scales and clinically meaningful comparison to reference intervals.

### 4.3 Layer 3: Feature Generation

The third layer derives clinically meaningful features from normalized biomarker values. These features include binary flags, severity indicators, and ratio-based patterns. Importantly, these features are not used as direct diagnoses; rather, they act as structured evidence that supports downstream reasoning.

### 4.4 Layer 4: Graph Reasoning

The fourth layer performs inference over a domain-specific knowledge graph. Biomarkers, thresholds, diseases, findings, and recommendations are represented as nodes and relationships. Reasoning over this graph allows the system to connect abnormal values to candidate clinical findings and evidence chains. This stage introduces explainability because the resulting output can be linked to specific graph traversals rather than opaque classifications.

### 4.5 Layer 5: Confidence Validation

The fifth layer validates the plausibility and urgency of each finding. It checks for impossible combinations, applies clinical rules, calibrates confidence, and flags cases that may require clinician review. This stage reduces false confidence and makes the system more reliable in real-world clinical use.

### 4.6 Layer 6: Presentation

The final layer uses a language model only for communication and summarization. It converts validated findings into patient-friendly and clinician-facing reports while preserving the deterministic findings generated by earlier layers. The language model does not invent diagnoses or alter the underlying evidence; it only reformulates certified outputs.

## 5. Architecture and Design Principles

ENERVERA is built around several design principles:

- Transparency: every layer produces explicit intermediate outputs.
- Modularity: panel-specific knowledge is isolated in domain modules.
- Determinism where possible: normalization, feature generation, and validation are rule-based and auditable.
- Controlled intelligence: the language model is used only as a presentation layer.
- Extensibility: new biomarkers, thresholds, or panels can be incorporated by extending domain data rather than rewriting core logic.

The architecture is organized as a backend service with a REST API that accepts either structured biomarker values or uploaded laboratory images. The orchestration layer coordinates the pipeline and preserves audit trails for each stage.

## 6. Methodology

The development of ENERVERA follows a layered software engineering approach inspired by clinical decision support design principles. The pipeline begins with OCR-based extraction, followed by normalization against canonical biomarker definitions and reference ranges. The system then generates structured features and reasons over a knowledge graph to infer clinically relevant findings. Confidence validation is applied before the final report is generated.

A key methodological decision is the separation of inference from presentation. This architecture ensures that medical reasoning is grounded in deterministic clinical logic rather than unconstrained language-model generation. The language model is treated as a reporting interface, not as the primary source of medical interpretation.

The system also emphasizes domain modularity. CBC-specific rules, biomarkers, thresholds, and validation logic are stored in dedicated modules, allowing the framework to be adapted to other laboratory panels in the future.

## 7. Evaluation Strategy and Validation Approach

Although the present manuscript presents the conceptual and implementation framework of ENERVERA, future empirical evaluation should include:

- Comparison against clinician-annotated CBC interpretation outcomes.
- Error analysis for OCR extraction accuracy and normalization robustness.
- Evaluation of reasoning consistency across abnormal and borderline cases.
- Human factors studies assessing usability for clinicians and patients.
- Safety analysis to ensure that the system does not overstate certainty or generate unsupported conclusions.

The current implementation includes testable components for OCR parsing, normalization, feature generation, graph reasoning, validation, and presentation generation, enabling future formal benchmarking and ablation studies.

## 8. Discussion

ENERVERA contributes to the field of medical informatics by presenting a transparent alternative to fully black-box clinical interpretation systems. Its architecture is particularly suitable for environments where interpretability, traceability, and auditability are essential. By combining symbolic reasoning with controlled language generation, the system addresses common concerns around trust and clinical safety.

The system is also valuable from an engineering perspective. The modular design enables incremental extension, easier maintenance, and cross-panel generalization. Because the model logic is separated from the presentation layer, the system can be updated without changing the user-facing report format or the underlying clinical evidence.

However, several limitations remain. OCR accuracy may degrade for low-quality scans or non-standard layouts. Reference range logic requires careful handling of demographic and contextual factors. Knowledge graphs must be curated and updated continuously to remain clinically relevant. Finally, any clinical decision support system must be evaluated in real-world settings before deployment.

## 9. Conclusion

ENERVERA presents a practical and principled approach to automated CBC interpretation. By organizing the workflow into distinct layers for extraction, normalization, feature generation, graph reasoning, validation, and presentation, the system balances technical robustness with clinical explainability. The framework demonstrates that it is possible to build healthcare-oriented AI systems that are both intelligent and accountable.

Future work will focus on expanding the knowledge graph, improving OCR robustness, validating performance against real clinical data, and extending the architecture to additional laboratory panels. In doing so, ENERVERA may serve as a foundation for next-generation clinical decision support systems that are transparent, extensible, and clinically meaningful.

## References

1. Friedman, C. et al. Clinical decision support systems: a review of current applications and challenges.
2. Bui, A. A. T. et al. Clinical decision support and AI in healthcare: opportunities and pitfalls.
3. Raghupathi, W. and Raghupathi, V. Big data analytics in healthcare: promise and potential.
4. Rajkomar, A. et al. Machine learning in medicine.
5. Swain, D. and others. Knowledge graph approaches for biomedical reasoning and decision support.
6. Open-source and academic literature on OCR for medical document understanding and laboratory report parsing.
