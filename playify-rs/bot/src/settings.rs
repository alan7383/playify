//! Persistent per-guild settings (JSON file, mirrors v2's guild_settings).

use std::{collections::HashMap, path::PathBuf, sync::Arc};

use serde::{Deserialize, Serialize};
use tracing::warn;

#[derive(Default, Clone, Serialize, Deserialize)]
pub struct GuildSettings {
    #[serde(default)]
    pub default_volume: Option<f32>,
    #[serde(default)]
    pub mode_24_7: String, // "off" | "normal" | "auto"
    #[serde(default)]
    pub kawaii: bool,
    #[serde(default)]
    pub allowed_channels: Vec<u64>,
}

#[derive(Clone)]
pub struct Settings {
    path: PathBuf,
    inner: Arc<std::sync::Mutex<HashMap<u64, GuildSettings>>>,
}

impl Settings {
    pub fn load() -> Self {
        let path = ["../data", "data"]
            .iter()
            .map(PathBuf::from)
            .find(|dir| dir.is_dir())
            .unwrap_or_else(|| PathBuf::from("data"))
            .join("v3_settings.json");
        let map = std::fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default();
        Settings {
            path,
            inner: Arc::new(std::sync::Mutex::new(map)),
        }
    }

    pub fn get(&self, guild_id: u64) -> GuildSettings {
        self.inner
            .lock()
            .expect("settings lock")
            .get(&guild_id)
            .cloned()
            .unwrap_or_default()
    }

    pub fn update(&self, guild_id: u64, mutate: impl FnOnce(&mut GuildSettings)) {
        let snapshot = {
            let mut map = self.inner.lock().expect("settings lock");
            mutate(map.entry(guild_id).or_default());
            serde_json::to_string_pretty(&*map).unwrap_or_default()
        };
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Err(e) = std::fs::write(&self.path, snapshot) {
            warn!("cannot save settings: {e}");
        }
    }
}
