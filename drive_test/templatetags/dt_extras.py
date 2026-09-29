from django import template

register = template.Library()


@register.filter
def dictget(mapping, key):
    """Look up ``mapping[key]`` in a template (dicts aren't subscriptable by
    a variable key in the Django template language).
    """
    if hasattr(mapping, 'get'):
        return mapping.get(key)
    return None
