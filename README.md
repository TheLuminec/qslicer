# Quantum Program Analysis Framework

## Overview

This project works as a static analysis system for quantum programs, providing a framework for parsing, slicing, and representing programs. It introduces a frontend-agnostic intermediate representation (IR), constructs a Quantum Program Dependence Graph (QPDG), and enables program slicing based on quantum and classical dependencies. The parser supports multiple quantum language frontends with a unified backend for consistent analysis.

---

[[_TOC_]]

---

## Modules

### 1. `parser.py`

#### Purpose

Establishes the abstract base class for all quantum language parsers and converts the frotend code into a `QuantumProgram` IR.

---

### 2. `qinstruction.py`

#### Purpose

Represents a single quantum operation in the internal representation.

---

### 3. `qprogram.py`

#### Purpose

Defines the canonical representation of a quantum program for dependency graph construction.

---

### 4. `qpdg.py`

#### Purpose

Builds a dependency graph capturing relationships between quantum operations.

---

### 5. `slicer.py`

#### Purpose

Provides program slicing capabilities over the QPDG.
