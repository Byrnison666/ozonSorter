import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { DefaultTheme, NavigationContainer, type Theme } from '@react-navigation/native';
import { StatusBar } from 'expo-status-bar';
import { Pressable, StyleSheet, View } from 'react-native';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppProvider, useApp } from './src/app/AppContext';
import { ClientsScreen } from './src/screens/ClientsScreen';
import { DashboardScreen } from './src/screens/DashboardScreen';
import { ImportLogScreen } from './src/screens/ImportLogScreen';
import { SettingsScreen } from './src/screens/SettingsScreen';
import { StaleScreen } from './src/screens/StaleScreen';
import { Icon, type IconName, Text } from './src/ui/components';
import { colors, fontFamily, type } from './src/ui/theme';

const Tab = createBottomTabNavigator();

const theme: Theme = {
  ...DefaultTheme,
  colors: { ...DefaultTheme.colors, primary: colors.primary, background: colors.bg, card: colors.surface, text: colors.text, border: colors.border },
  fonts: {
    regular: { fontFamily, fontWeight: '400' },
    medium: { fontFamily, fontWeight: '500' },
    bold: { fontFamily, fontWeight: '600' },
    heavy: { fontFamily, fontWeight: '700' },
  },
};

/** Кнопка синхронизации в шапке: цвет — есть ли невыложенные изменения. */
function SyncButton() {
  const app = useApp();
  if (!app.configured) return null;
  const clean = app.dirty === false;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={app.dirty ? 'Синхронизировать: есть невыложенные изменения' : 'Синхронизировать'}
      onPress={() => app.syncNow({ announce: true })}
      disabled={app.syncBusy}
      hitSlop={8}
      style={({ pressed }) => [s.bubble, !clean && s.bubbleDirty, pressed && s.bubblePressed]}
    >
      <Icon name={clean ? 'cloud' : 'upload-cloud'} size={20} color={clean ? colors.success : colors.warningText} />
    </Pressable>
  );
}

const TABS: Array<{ name: string; title: string; icon: IconName; component: () => React.JSX.Element }> = [
  { name: 'dashboard', title: 'Главная', icon: 'home', component: DashboardScreen },
  { name: 'clients', title: 'Клиенты', icon: 'users', component: ClientsScreen },
  { name: 'stale', title: 'Залежалые', icon: 'clock', component: StaleScreen },
  { name: 'log', title: 'Журнал', icon: 'list', component: ImportLogScreen },
  { name: 'settings', title: 'Настройки', icon: 'settings', component: SettingsScreen },
];

function Tabs() {
  const insets = useSafeAreaInsets();
  return (
    <Tab.Navigator
      screenOptions={{
        headerRight: () => <SyncButton />,
        headerRightContainerStyle: { paddingRight: 16 },
        headerTitleAlign: 'left',
        headerTitleStyle: { ...type.title, fontFamily, color: colors.text },
        headerStyle: { backgroundColor: colors.bg },
        headerShadowVisible: false,
        tabBarActiveTintColor: colors.primaryText,
        tabBarInactiveTintColor: colors.textSecondary,
        // Пилюля активной вкладки (56×30) и подпись не помещаются в стандартные 49dp.
        tabBarStyle: { height: 66 + insets.bottom, paddingTop: 8 },
        tabBarItemStyle: { paddingVertical: 4 },
        tabBarIconStyle: { width: 56, height: 30 },
      }}
    >
      {TABS.map((t) => (
        <Tab.Screen
          key={t.name}
          name={t.name}
          component={t.component}
          options={{
            title: t.name === 'stale' ? 'Залежавшиеся посылки' : t.name === 'log' ? 'Журнал импортов' : t.title,
            tabBarLabel: ({ focused, color }) => (
              <Text style={[s.tabLabel, { color }, focused && s.tabLabelActive]}>{t.title}</Text>
            ),
            tabBarIcon: ({ focused, color }) => (
              <View style={[s.pill, focused && s.pillActive]}>
                <Icon name={t.icon} size={22} color={color} />
              </View>
            ),
          }}
        />
      ))}
    </Tab.Navigator>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <AppProvider>
        <NavigationContainer theme={theme}>
          <Tabs />
        </NavigationContainer>
        <StatusBar style="dark" />
      </AppProvider>
    </SafeAreaProvider>
  );
}

const s = StyleSheet.create({
  bubble: {
    width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center',
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border,
  },
  bubbleDirty: { backgroundColor: colors.warningSoft, borderColor: colors.warningSoft },
  bubblePressed: { backgroundColor: colors.surfaceAlt },
  pill: { width: 56, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
  pillActive: { backgroundColor: colors.primarySoft },
  tabLabel: { ...type.caption, marginTop: 4 },
  tabLabelActive: { fontWeight: '500' },
});
