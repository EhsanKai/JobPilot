"""Provider-agnostic AI layer (V0.2+).

Business logic depends only on `AIProvider` and the `AIError` hierarchy in
`app.ai.provider`. Vendor SDKs are imported exclusively inside
`app.ai.providers.*`, so swapping or adding a provider never touches the
resume parser, services, or API layer.
"""
