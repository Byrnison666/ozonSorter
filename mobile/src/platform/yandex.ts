/** Сеть Яндекса на телефоне: XMLHttpRequest React Native и секрет приложения из сборки. */
import { xhrTransport } from '../core/sync/yadisk';
import type { OAuthEnv } from '../core/sync/yandexOAuth';

export const transport = xhrTransport();

export const oauthEnv: OAuthEnv = {
  transport,
  // Подставляется при сборке из mobile/.env.local; обращение — только через точку,
  // иначе Expo не подставит значение.
  clientSecret: process.env.EXPO_PUBLIC_YANDEX_CLIENT_SECRET ?? '',
};
