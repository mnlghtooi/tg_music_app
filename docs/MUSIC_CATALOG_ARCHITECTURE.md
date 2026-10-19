# Music Lab: архитектура каталога и источников (v0.7.10)

## Цель

Перед добавлением VK отделить **поиск и метаданные** от **права и технической возможности воспроизведения**. Сохранить SoundCloud/Bandcamp/Audius, прежние плейлисты `musiclab_library_v1`, API старой карточки и mini-player. Архитектуру развивать небольшими PR, а не одномоментно переписывать весь сервер и UI.

## Реализовано в первом PR

```text
      Music Lab artist screen
               |
GET /api/artists/merged?source=...&id=...
               |
      ArtistAggregateService
        /                \
EntityService        CatalogService
(provider-owned)     (provider search)
        \                /
        Provider registry
  SoundCloud | Bandcamp | Audius
             |
   TrackAvailability / player
          (unchanged)
```

- `music_providers.py`: реестр поддерживаемых источников и возможностей. `search_tracks`/`artist_profiles` **не равны** `playback`. VK **не** включён.
- `artist_aggregation.py`: выдаёт исходный authoritative `EntityService.overview` плюс отдельный блок `section=discovered` с **кандидатами**, найденными в двух других сервисах. Кандидаты помечаются `discovery=artist_name_only` и `verified_artist=false`. У нас нет проверенного межсервисного artist ID — совпадение имени не считается доказательством принадлежности записи.
- `CatalogService` берёт источники из реестра; поиск одного провайдера переиспользуется, а не переписывается.
- `/api/artist` остаётся source-owned, `/api/artists/merged` — агрегированный экран. Оба защищены прежней локальной cookie.
- Frontend показывает секцию «Из других музыкальных сервисов» с маркировкой неопределённости, исходным провайдером и кнопкой воспроизведения. Плеер не останавливается при открытии/закрытии страницы. Очередь для артистов дедуплицируется только по `source:id`; межсервисные кандидаты **не** склеиваются с оригиналами.
- Явно заблокированные и preview-only записи фильтруются прежним `track_availability.js`; `unknown` остаётся возможным кандидатом, но без гарантии настоящего аудиопотока.
- Отказ одного чужого провайдера — частичный результат с `failed_sources`; исходная карточка продолжает открываться.
- Бэкенд ограничивает дополнительный поиск максимум двумя страницами на источник, до 20 найденных записей, кеширует результат на 90 секунд; прежние лимиты и cooldown действуют.

**Ограничения честно:** это ещё **не** подтверждённая объединённая дискография и не MusicBrainz canonical recording resolution. Треки могут быть каверами, ремиксами и неполными загрузками, даже если имя автора совпало. Физическое наличие файла и разрешение на воспроизведение подтверждаются отдельно. Дополнительная выдача не гарантирует, что у Portishead станет значительно больше полного аудио; реальный результат проверяется на iPhone и в источниках.

## Контракт

`GET /api/artists/merged?source=soundcloud&id=<source-artist-id>`

Возвращает совместимое с `/api/artist` тело:

```json
{
  "profile": {"source": "soundcloud", "kind": "artist", "id": "123", "title": "Portishead"},
  "blocks": [
    {"section": "popular", "data": [], "has_more": false},
    {"section": "albums", "data": [], "has_more": false},
    {"section": "discovered", "data": [{"source": "audius", "kind": "track", "id": "xyz", "discovery": "artist_name_only", "verified_artist": false, "availability": "unknown"}], "has_more": false}
  ],
  "aggregation": {
    "status": "complete",
    "searched_sources": ["bandcamp", "audius"],
    "failed_sources": [],
    "candidate_count": 1,
    "identity_verified": false
  }
}
```

При частичных ошибках `status=partial`, `failed_sources` содержит только коды провайдеров (не текст исключений и не секреты).

## План архитектуры до VK

1. **Приёмка этого PR на iPhone.** Portishead, Massive Attack, Radiohead: подсказки из других сервисов, отсутствие ложного смешивания артистов, прослушивание кандидата, отсутствие остановок музыки, правильное сообщение при сбое источника.
2. **Canonical catalog / recordings.** В отдельном модуле представить `Artist`, `Release`, `Recording`, `ProviderRecording`, `PlaybackAvailability`. MusicBrainz MBID / ISRC использовать только после проверки; полное совпадение имени/длительности само по себе не склеивает идентичности. Оригинальные source IDs сохраняются.
3. **Больше покрытия с существующими провайдерами:** оптимизация поиска с безопасной пагинацией, внешние verified artist links, обнаружение других легальных источников; сравнение 20 артистов с данными `full playable`, `unknown`, `external_only`.
4. **Плагинная граница воспроизведения:** адаптер `PlaybackProvider` отвечает за разрешённый in-app поток, `CatalogProvider` — только за метаданные. Функции с только каталогом не представлять как полноценный audio provider.
5. **Потом VK:** metadata-only spike за feature flag и реальные измерения без загрузки секретов/аудио в GitHub; включать in-app только при подтверждённом разрешённом способе воспроизведения.

## Регрессия и безопасность

- Тесты `test_artist_aggregation.py`, `test_artist_catalog.cjs` + все прежние Python/Node в CI.
- Работа поверх v0.7.9 и проверки блока/превью, без сброса `localStorage`.
- Не изменять существующую папку Windows вне согласованного обновления локального агента; не создавать ZIP/новые установки.
- Не выдавать прохождение CI за работающий поток на реальном iPhone.
- Новые источники нельзя подключать только ради числа метаданных: ориентир — **уникальные полноразмерные, фактически воспроизводимые записи**.
