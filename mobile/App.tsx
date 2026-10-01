import Ionicons from '@expo/vector-icons/Ionicons';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { DefaultTheme, NavigationContainer } from '@react-navigation/native';
import { StatusBar } from 'expo-status-bar';
import type { ComponentProps } from 'react';
import { Pressable } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AppProvider, useApp } from './src/app/AppContext';
import { ClientsScreen } from './src/screens/ClientsScreen';
import { DashboardScreen } from './src/screens/DashboardScreen';
import { ImportLogScreen } from './src/screens/ImportLogScreen';
import { SettingsScreen } from './src/screens/SettingsScreen';
import { StaleScreen } from './src/screens/StaleScreen';
import { colors } from './src/ui/theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

const Tab = createBottomTabNavigator();

const theme = {
  ...DefaultTheme,
  colors: { ...DefaultTheme.colors, primary: colors.primary, background: colors.bg, card: colors.surface, text: colors.text, border: colors.border },
};

/** Кнопка синхронизации в шапке: цвет — есть ли невыложенные изменения. */
function SyncButton() {
  const app = useApp();
  if (!app.configured) return null;
  const color = app.dirty === false ? colors.success : colors.warningText;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={app.dirty ? 'Синхронизировать: есть невыложенные изменения' : 'Синхронизировать'}
      onPress={() => app.syncNow({ announce: true })}
      disabled={app.syncBusy}
      hitSlop={12}
      style={{ paddingHorizontal: 16 }}
    >
      <Ionicons name={app.dirty === false ? 'cloud-done-outline' : 'cloud-upload-outline'} size={24} color={color} />
    </Pressable>
  );
}

const TABS: Array<{ name: string; title: string; icon: IconName; component: () => React.JSX.Element }> = [
  { name: 'dashboard', title: 'Главная', icon: 'home-outline', component: DashboardScreen },
  { name: 'clients', title: 'Клиенты', icon: 'people-outline', component: ClientsScreen },
  { name: 'stale', title: 'Залежалые', icon: 'time-outline', component: StaleScreen },
  { name: 'log', title: 'Журнал', icon: 'list-outline', component: ImportLogScreen },
  { name: 'settings', title: 'Настройки', icon: 'settings-outline', component: SettingsScreen },
];

export default function App() {
  return (
    <SafeAreaProvider>
      <AppProvider>
        <NavigationContainer theme={theme}>
          <Tab.Navigator
            screenOptions={{
              headerRight: () => <SyncButton />,
              tabBarActiveTintColor: colors.primary,
              tabBarInactiveTintColor: colors.textSecondary,
              tabBarLabelStyle: { fontSize: 11 },
            }}
          >
            {TABS.map((t) => (
              <Tab.Screen
                key={t.name}
                name={t.name}
                component={t.component}
                options={{
                  title: t.name === 'stale' ? 'Залежавшиеся посылки' : t.name === 'log' ? 'Журнал импортов' : t.title,
                  tabBarLabel: t.title,
                  tabBarIcon: ({ color, size }) => <Ionicons name={t.icon} size={size} color={color} />,
                }}
              />
            ))}
          </Tab.Navigator>
        </NavigationContainer>
        <StatusBar style="dark" />
      </AppProvider>
    </SafeAreaProvider>
  );
}
