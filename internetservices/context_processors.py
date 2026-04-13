# context processor to add global settings to all templates

def global_settings(request):
    """
    Context processor to add global settings to all templates.
    """
    return {
        'SITE_NAME': 'JS Technology',
        'SITE_DESCRIPTION': 'JS Technology is a leading provider of internet services in Tanzania.',
        'SITE_KEYWORDS': 'internet, technology, services, Tanzania',
        'COMPANY_NAME': 'JS Technology',
        'COMPANY_ADDRESS': 'P.O Box 1397, Moshi, Tanzania',
        'COMPANY_PHONE': '+255 719 562 050',
        'CURRENT_YEAR': '2025',
        'CONTACT_EMAIL': 'jsafaritechnology@gmail.com',
        'SOCIAL_MEDIA': {
            'facebook': 'https://www.instagram.com/j_s_technology/',
            'twitter': 'https://www.instagram.com/j_s_technology/',
            'instagram': 'https://www.instagram.com/j_s_technology/',
        }
    }