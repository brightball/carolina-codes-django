class PolyglotHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        from catalog import db

        response["X-Polyglot-Language"] = db.LANGUAGE
        response["X-Polyglot-Framework"] = db.FRAMEWORK
        return response
