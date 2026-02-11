# Step 11 – ML Training (Regression auf LOS)
**Layer:** Marts → ML  
**Ziel:** Trainieren und bewerten eines Regressionsmodells zur Vorhersage der Aufenthaltsdauer (LOS) auf Basis der erzeugten Features.

---

## 1. Zweck und Rolle

Dieser Schritt operationalisiert Data Governance im ML-Kontext:
- reproduzierbare Trainingspipeline,
- dokumentierte Metriken,
- Artefakt-Versionierung (Model Pickle + Summary + Charts).

Input ist der finale Mart:
- `mart_merged_for_lm`

---

## 2. Preconditions
- DuckDB Tabelle `mart_merged_for_lm` muss existieren und nicht leer sein.
- run_id wird aus Manifesten abgeleitet (Marts > ADaM > SDTM fallback).

---

## 3. Dataset-Checks / EDA Outputs

### 3.1 Dataset Overview
- rows, cols, cells als schneller Größencheck.

### 3.2 Numeric Spread (IQR)
- berechnet IQR pro numerischer Spalte
- normalisiert IQR (0–1)
- visualisiert als Heatmap

Ziel:
- Features mit nahezu 0 Varianz identifizieren (potentiell uninformativ).

---

## 4. Feature Handling & Leakage-Schutz

### 4.1 Drop “no observed values” Features
Erkennt Spalten, die nach numeric coercion **keinen einzigen nicht-NA Wert** in X_train haben.
Diese Features werden entfernt (train-only Entscheidung, um Imputer-Probleme zu verhindern).

Ziel:
- verhindert sklearn Median-Imputer Fehler (“no observed values”).

### 4.2 Preprocessing Pipeline
- Numeric: Imputer + Scaling (z.B. Standard/MinMax je Konfiguration)
- Categorical: Imputer + OneHotEncoder
- ColumnTransformer in sklearn Pipeline

---

## 5. Model Training & Evaluation

### 5.1 Holdout Split
- train_test_split zur finalen Holdout-Evaluation.

### 5.2 Cross Validation
- KFold + cross_validate für Modellvergleich.

### 5.3 Modelle
- RandomForestRegressor
- GradientBoostingRegressor

### 5.4 Metriken
- MAE
- RMSE
- R²

Output:
- model_compare_cv.csv
- holdout_predictions.csv

---

## 6. Artefakte / Reproduzierbarkeit

- best_model.pkl (joblib)
- ml_summary.json (Metriken, Feature Drops, Parameter)
- Charts:
  - Target Histogram
  - CV RMSE
  - Pred vs True
  - Residuals & Residual Histogram

- manifest_ml.json (Artefakt-Referenzen)

---

## 7. Bedeutung im Projektkontext
Dieser Step zeigt:
- Ende-zu-Ende: Daten → Standardisierung → Analyse → ML
- Governance-as-Code: Metriken/Plots/Artefakte sind prüfbar u
