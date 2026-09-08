import os
import sys
import uvicorn
from config.settings import settings

try:
    import typer
    _has_typer = True
except ImportError:
    _has_typer = False


def _serve():
    print(f"Starting key-router proxy on {settings.host}:{settings.port}")
    uvicorn.run("proxy.server:app", host=settings.host, port=settings.port, reload=True)


def main():
    if not _has_typer or len(sys.argv) == 1:
        _serve()
        return
    app = _build_typer_app()
    app()


def _build_typer_app():
    app = typer.Typer(add_completion=False, no_args_is_help=False, help="key-router — custom provider CLI")

    @app.command("serve")
    def serve():
        _serve()

    @app.command("add-provider")
    def add_provider(
        id: str = typer.Option(..., "--id", help="Provider ID (lowercase, 2-32 chars)"),
        display_name: str = typer.Option(..., "--display-name", help="Display name"),
        api: str = typer.Option("openai_compatible", "--api", help="openai_compatible or anthropic"),
        base_url: str = typer.Option(..., "--base-url", help="Base URL e.g. https://api.example.com/v1"),
        api_key_env: str = typer.Option(None, "--api-key-env", help="ENV var name for API key"),
        api_key: str = typer.Option(None, "--api-key", help="Alias for --api-key-env (ENV var name)"),
        header: list[str] = typer.Option(None, "--header", help="Extra header K=V (repeatable)"),
        model: list[str] = typer.Option(None, "--model", help="Model ID:Name:reasoning:image (repeatable)"),
    ):
        from config.custom_providers import save_provider, normalize_api_key_input, validate_spec
        env_name = api_key_env or api_key or ""
        if not env_name:
            typer.echo("Error: --api-key-env (or --api-key) is required", err=True)
            raise typer.Exit(1)
        env_name = normalize_api_key_input(env_name)
        headers = {}
        for h in header or []:
            if "=" not in h:
                typer.echo(f"Invalid --header '{h}' (expected K=V)", err=True)
                raise typer.Exit(1)
            k, v = h.split("=", 1)
            headers[k.strip()] = v.strip()
        models = []
        for m in model or []:
            parts = m.split(":")
            mid = parts[0].strip() if len(parts) > 0 else ""
            mname = parts[1].strip() if len(parts) > 1 else mid
            reasoning = (parts[2].strip().lower() in ("1", "true", "yes") if len(parts) > 2 and parts[2].strip() else False)
            image = (parts[3].strip().lower() in ("1", "true", "yes") if len(parts) > 3 and parts[3].strip() else False)
            if not mid:
                typer.echo(f"Invalid --model '{m}'", err=True)
                raise typer.Exit(1)
            models.append({"id": mid, "name": mname or mid, "reasoning": reasoning, "image": image})
        if not models:
            typer.echo("At least one --model is required", err=True)
            raise typer.Exit(1)
        spec = {
            "id": id.strip().lower(),
            "display_name": display_name.strip(),
            "provider_api": api,
            "base_url": base_url.strip().rstrip("/"),
            "api_key_env": env_name,
            "headers": headers,
            "models": models,
        }
        try:
            validate_spec(spec)
        except ValueError as e:
            typer.echo(f"Validation error: {e}", err=True)
            raise typer.Exit(1)
        if not os.environ.get(env_name):
            typer.echo(f"Warning: ENV {env_name} is not set", err=True)
        save_provider(spec)
        typer.echo(f"Provider '{spec['id']}' saved ({len(models)} model(s), api_key_env={env_name})")

    @app.command("list-providers")
    def list_providers():
        from config.custom_providers import get_masked_providers
        providers = get_masked_providers()
        if not providers:
            typer.echo("No custom providers")
            return
        for pid, spec in providers.items():
            hk = "has_key" if spec.get("has_key") else "missing ENV"
            mids = ", ".join(m.get("id", "") for m in spec.get("models", []))
            typer.echo(f"{pid} | {spec.get('display_name')} | {spec.get('provider_api')} | {spec.get('base_url')} | {hk} | models: {mids}")

    @app.command("remove-provider")
    def remove_provider(id: str = typer.Argument(..., help="Provider ID")):
        from config.custom_providers import delete_provider
        ok = delete_provider(id.strip().lower())
        if not ok:
            typer.echo(f"Provider '{id}' not found", err=True)
            raise typer.Exit(1)
        typer.echo(f"Provider '{id}' removed")

    @app.command("list-models")
    def list_models(provider: str = typer.Argument(..., help="Provider ID")):
        import httpx
        from config.custom_providers import load_custom_providers
        spec = load_custom_providers().get(provider)
        if not spec:
            typer.echo(f"Provider '{provider}' not found", err=True)
            raise typer.Exit(1)
        api_key = os.environ.get(spec.get("api_key_env", ""), "") if spec.get("api_key_env") else ""
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        if spec.get("headers"):
            headers.update(spec["headers"])
        base = spec.get("base_url", "").rstrip("/")
        candidates = [f"{base}/models", f"{base}/v1/models"] if not base.endswith("/models") else [base]
        for url in candidates:
            try:
                resp = httpx.get(url, headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    ids = []
                    if isinstance(data.get("data"), list):
                        ids = [m.get("id") for m in data["data"] if isinstance(m, dict) and m.get("id")]
                    elif isinstance(data, list):
                        ids = [str(x) for x in data]
                    if ids:
                        for i in ids:
                            typer.echo(i)
                        return
            except Exception:
                continue
        mids = [m.get("id") for m in spec.get("models", [])]
        typer.echo(f"(local models for {provider}: {', '.join(mids) or 'none'})")
        for m in mids:
            typer.echo(m)

    @app.callback(invoke_without_command=True)
    def root(ctx: typer.Context):
        if ctx.invoked_subcommand is None and len(sys.argv) == 1:
            _serve()

    return app


if __name__ == "__main__":
    main()
