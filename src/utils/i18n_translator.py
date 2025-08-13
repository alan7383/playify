import yaml
import re
import pathlib
from enum import Enum

# Define a simple Enum for supported locales
class Locale(Enum):
    EN_US = 'en-US'
    EN_X_KAWAII = 'en-x-kawaii'

class I18nTranslator:
    """
    A class to handle internationalization (i18n) translations.
    It loads translations from YAML files and provides methods to translate keys.
    It supports variable replacement and formatting in translations.
    Wrote it because available libraries didnt work as expected and got annoyed.
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
        """
        return self.translate(key, locale, **kwargs)

    def refresh_translations(self, translations_dir) -> None:
        """
        Refresh the translations by reloading them from the specified directory.
        """
        self.__translations.clear()
        self.load_translations(translations_dir)