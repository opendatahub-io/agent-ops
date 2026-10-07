// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Gateway interceptor that registers each OpenShell sandbox's SPIFFE ID as a
//! Keycloak federated client when the sandbox is created, and removes it when
//! the sandbox is deleted.

pub mod auth;
pub mod config;
pub mod keycloak;
pub mod service;
pub mod spiffe;
