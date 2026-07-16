//! Translations, reusing the repo's existing i18n/*.yml files (same keys
//! and {placeholders} as the Python bot). Two locales: en-US and the
//! en-x-kawaii flavor toggled per guild by /kaomoji.

use std::{collections::HashMap, path::PathBuf, sync::OnceLock};

fn flatten(prefix: &str, value: &serde_yaml::Value, out: &mut HashMap<String, String>) {
    match value {
        serde_yaml::Value::Mapping(map) => {
            for (key, child) in map {
                let Some(key) = key.as_str() else { continue };
                let path = if prefix.is_empty() {
                    key.to_string()
                } else {
                    format!("{prefix}.{key}")
                };
                flatten(&path, child, out);
            }
        }
        serde_yaml::Value::String(text) => {
            out.insert(prefix.to_string(), text.clone());
        }
        other => {
            if let Some(text) = other.as_i64().map(|n| n.to_string()) {
                out.insert(prefix.to_string(), text);
            }
        }
    }
}

fn load_locale(name: &str) -> HashMap<String, String> {
    let path = ["../i18n", "i18n", "../../i18n"]
        .iter()
        .map(|dir| PathBuf::from(dir).join(format!("{name}.yml")))
        .find(|p| p.exists());
    let Some(path) = path else {
        return HashMap::new();
    };
    let Ok(text) = std::fs::read_to_string(&path) else {
        return HashMap::new();
    };
    let Ok(value) = serde_yaml::from_str::<serde_yaml::Value>(&text) else {
        return HashMap::new();
    };
    let mut out = HashMap::new();
    flatten("", &value, &mut out);
    out
}

fn tables() -> &'static (HashMap<String, String>, HashMap<String, String>) {
    static TABLES: OnceLock<(HashMap<String, String>, HashMap<String, String>)> =
        OnceLock::new();
    TABLES.get_or_init(|| (load_locale("en-US"), load_locale("en-x-kawaii")))
}

/// Looks up a translation key; kawaii locale falls back to en-US, and a
/// missing key falls back to the key itself (never panics on drift).
pub fn t(kawaii: bool, key: &str) -> String {
    let (english, kawaii_table) = tables();
    if kawaii {
        if let Some(text) = kawaii_table.get(key) {
            return text.clone();
        }
    }
    english.get(key).cloned().unwrap_or_else(|| key.to_string())
}

/// t() plus `{name}` placeholder substitution.
#[allow(dead_code)] // public i18n API for the ongoing string migration
pub fn tf(kawaii: bool, key: &str, vars: &[(&str, &str)]) -> String {
    let mut text = t(kawaii, key);
    for (name, value) in vars {
        text = text.replace(&format!("{{{name}}}"), value);
    }
    text
}
