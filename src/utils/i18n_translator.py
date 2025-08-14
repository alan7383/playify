import yaml
import re
import pathlib
from enum import Enum

# Define a simple Enum for supported locales
class Locale(Enum):
    EN_US = 'en-US' # English (United States)
    FR_FR = 'fr-FR' # French (France)
    DE_DE = 'de-DE' # German (Germany)
    EN_X_KAWAII = 'en-x-kawaii' # English (Kawaii variant)

def fuzzy_format(value: str) -> str:
    """
    Format a string by capitalizing each second letter
    """
    return ''.join(
        char.upper() if i % 2 == 0 else char.lower()
        for i, char in enumerate(value)
    )

class I18nTranslator:
    """
    A class to handle internationalization (i18n) translations.
    It loads translations from YAML files and provides methods to translate keys.
    It supports variable replacement and formatting in translations.
    Wrote it because available libraries didnt work as expected and got annoyed.

    i18n variables:
    - {var} for simple variable replacement
    - {var|upper} for uppercasing the variable
    - {var|lower} for lowercasing the variable
    - {var|fuzzy} for fuzzy formatting the variable (eg. capitalizing every second letter)

    Example usage:
        translator = I18nTranslator(default_locale='en-US', translations_dir='i18n')
        result = translator.translate('error.critical.title') # Output: "An error occurred"
        french_result = translator.translate('error.critical.title', locale='fr-FR') # Output: "Une erreur s'est produite"

    Attributes:
        default_locale (Locale): The default locale to use for translations.
        translations_dir (str): The directory where translation files are stored.
        verbose (bool): If True, prints debug information during translation loading.
        translations (dict): A dictionary containing loaded translations for each locale.
    Methods:
        get_current_default_locale(): Returns the current default locale.
        get_available_locales(): Returns a list of available locales based on loaded translations.
        load_translations(translations_dir): Loads translations from the specified directory.
        translate(key, locale=None, **kwargs): Translates a key using the loaded translations.
        t(key, locale=None, **kwargs): Short alias for translate method.
        refresh_translations(translations_dir): Refreshes the translations by reloading them from the specified directory.
    """
    def __init__(self, default_locale=Locale.EN_US, translations_dir='i18n', verbose=False) -> None:
        """
        Initialize the translator with the default locale and translations directory.
        """
        self.__default_locale: Locale = default_locale
        self.__translations: dict[str, dict] = {}
        self.__verbose: bool = verbose
        self.load_translations(translations_dir)

    def get_current_default_locale(self) -> Locale:
        """
        Get the current default locale.
        """
        return self.__default_locale

    def get_available_locales(self) -> list[str]:
        """
        Get a list of available locales based on loaded translations.
        """
        return list(self.__translations.keys())

    def load_translations(self, translations_dir) -> None:
        """
        Load all translations from the specified directory.
        """
        translations_path = pathlib.Path(translations_dir)
        for file in translations_path.glob('*.yml'):
            locale_name = file.stem
            if self.__verbose:
                print(f"Loading translations for locale: {locale_name}")
            with open(file, 'r', encoding='utf-8') as f:
                try:
                    translations = yaml.safe_load(f)
                    self.__translations[locale_name] = translations
                except yaml.YAMLError as e:
                    print(f"Error loading {file}: {e}")
        if self.__verbose:
            print(f"Available locales: {self.get_available_locales()}")

        if not self.__translations:
            raise ValueError("No translations found in the specified directory.")
        
        if not self.__default_locale.value in self.__translations:
            raise ValueError(f"Default locale '{self.__default_locale.value}' not found in translations.")

    def translate(self, key, locale=None, **kwargs) -> str:
        """
        Translate a key using the loaded translations.
        If the key does not exist, return the default value if provided.

        Args:
            key (str): The translation key to look up.
            locale (str, optional): The locale to use for translation. Defaults to the default locale.
            **kwargs: Additional keyword arguments for variable replacement in the translation string. (e.g., {var} or {var|upper})

        """
        if locale is None:
            locale = self.__default_locale
        translations = self.__translations.get(locale, {})

        keys = key.split('.')
        value = translations
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return key

        if isinstance(value, str):
            def replacer(match) -> str:
                var, fmt = match.group(1), match.group(2)
                val = kwargs.get(var, '')
                if fmt == 'upper':
                    return val.upper()
                elif fmt == 'lower':
                    return val.lower()
                elif fmt == 'fuzzy':
                    return fuzzy_format(val)
                return str(val)

            # Replace placeholders such as {var|upper} or {var}
            value = re.sub(r"\{(\w+)(?:\|(\w+))?\}", replacer, value)
            # Remove double (or more) spaces
            value = re.sub(r' +', ' ', value)
            # Remove leading/trailing spaces
            value = value.strip()
            return value

    def t(self, key, locale=None, **kwargs) -> str:
        """
        Short alias for translate method.
        Translate a key using the loaded translations.
        If the key does not exist, return the default value if provided.

        Args:
            key (str): The translation key to look up.
            locale (str, optional): The locale to use for translation. Defaults to the default locale.
            **kwargs: Additional keyword arguments for variable replacement in the translation string. (e.g., {var} or {var|upper})

        """
        return self.translate(key, locale, **kwargs)

    def refresh_translations(self, translations_dir) -> None:
        """
        Refresh the translations by reloading them from the specified directory.
        """
        self.__translations.clear()
        self.load_translations(translations_dir)
