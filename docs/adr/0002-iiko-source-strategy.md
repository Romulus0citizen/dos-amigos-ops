# ADR 0002: Adapter-based iiko integration

Status: Accepted

Use one adapter contract with interchangeable implementations:

- mock;
- server RestAPI;
- iikoCloud if required;
- automated export bridge as fallback.

Server RestAPI is the primary hypothesis until authentication is proven.
